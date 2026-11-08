"""Orchestrates the full run. Used by both the CLI and the Streamlit app."""
from __future__ import annotations

import copy
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from .llm import LLM
from .parse import Show, parse_show
from .report import write_csv, write_json, write_md, write_quality
from .steps import llm_steps as S
from .steps.rank import rank
from .steps.validate import Promo, dedupe, validate_promo

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> dict:
    cfg = yaml.safe_load(Path(path or ROOT / "config.yaml").read_text())
    if os.environ.get("PROMO_GEN_MODEL"):
        cfg["models"]["generator"] = os.environ["PROMO_GEN_MODEL"]
    if os.environ.get("PROMO_JUDGE_MODEL"):
        cfg["models"]["judge"] = os.environ["PROMO_JUDGE_MODEL"]
    return cfg


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "show"


def run(show: Show | str | Path, out_root: str | Path, cfg: dict | None = None, generator: str | None = None,
        judge_model: str | None = None, episodes: tuple[int, int] | None = None, k: int | None = None,
        use_cache: bool = True, api_keys: dict | None = None, progress=None, log=print,
        run_id: str | None = None, montages: int | None = None) -> dict:
    t_start = time.time()
    cfg = copy.deepcopy(cfg or load_config())
    if generator:
        cfg["models"]["generator"] = generator
    if judge_model:
        cfg["models"]["judge"] = judge_model
    pc = cfg["pipeline"]
    k = k or pc["candidates_per_episode"]
    n_montages = pc["montages"] if montages is None else montages
    gen, jud = cfg["models"]["generator"], cfg["models"]["judge"]
    fallback = cfg["models"].get("judge_fallback")
    gfb = cfg["models"].get("generator_fallback")
    repair_model = cfg["models"].get("repair") or gen
    progress = progress or (lambda stage, done, total, msg="": None)

    if not isinstance(show, Show):
        show = parse_show(show)
    if episodes:
        lo, hi = episodes
        show = Show(title=show.title, episodes=[e for e in show.episodes if lo <= e.number <= hi])
    if not show.episodes:
        raise ValueError("no episodes to process")

    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(out_root) / slug(show.title) / run_id
    out.mkdir(parents=True, exist_ok=True)
    llm = LLM(cfg, cache_dir=ROOT / ".cache", raw_dir=out / "raw", use_cache=use_cache, api_keys=api_keys, log=log)
    log(f"Run {run_id}: {len(show.episodes)} episodes, generator={gen}, judge={jud} -> {out}")

    # 1. series bible
    progress("bible", 0, 1, "Building series bible")
    bible = S.build_bible(llm, show, gen, gfb)
    S.save_json(out / "bible.json", bible.model_dump())
    progress("bible", 1, 1, "Series bible ready")

    # 2. per-episode candidates (parallel; rate limiter still applies)
    promos: list[Promo] = []
    moments: list[dict] = []
    failed_eps = []
    done = 0
    per_call = max(1, int(pc.get("episodes_per_call", 1)))
    windows = [show.episodes[i:i + per_call] for i in range(0, len(show.episodes), per_call)]
    progress("candidates", 0, len(windows), "Reading episodes")
    with ThreadPoolExecutor(max_workers=pc["max_workers"]) as ex:
        futs = {ex.submit(S.episode_candidates, llm, show, w, bible, gen, k, pc["generator_temperature"], gfb): w
                for w in windows}
        for f in as_completed(futs):
            w = futs[f]
            label = f"E{w[0].number:02d}" + (f"-E{w[-1].number:02d}" if len(w) > 1 else "")
            done += 1
            try:
                ps, kms = f.result()
                promos.extend(ps)
                for ep_n, km in kms:
                    moments.append({"episode": ep_n, **km.model_dump()})
                log(f"  {label}: {len(ps)} candidates, {len(kms)} key moments")
            except Exception as e:  # noqa: BLE001
                for ep in w:
                    failed_eps.append({"episode": ep.number, "error": str(e)[:300]})
                log(f"  {label}: FAILED {str(e)[:200]}")
            progress("candidates", done, len(windows), f"{label} done")

    # key-moment index for montages (drop moments with invalid beat IDs)
    idx = show.beat_index()
    clean_moments = []
    for m in sorted(moments, key=lambda x: (x["episode"], x["start_beat"])):
        a, b = idx.get(m["start_beat"].strip()), idx.get(m["end_beat"].strip())
        if not a or not b or a.episode != b.episode or b.idx < a.idx:
            continue
        ep = show.episode(a.episode)
        dur = max(x.end for x in ep.beats[a.idx:b.idx + 1]) - a.start
        clean_moments.append({**m, "start_beat": a.id, "end_beat": b.id, "duration": round(dur, 1),
                              "id": f"K{len(clean_moments) + 1:03d}"})
    S.save_json(out / "key_moments.json", clean_moments)

    # 3. cross-episode montages
    montage_promos = []
    if n_montages and len(show.episodes) > 1:
        progress("montage", 0, 1, "Composing cross-episode montages")
        try:
            montage_promos = S.build_montages(llm, show, bible, clean_moments, gen, n_montages, pc["generator_temperature"], gfb)
        except Exception as e:  # noqa: BLE001
            log(f"  montage step failed: {str(e)[:200]}")
        progress("montage", 1, 1, f"{len(montage_promos)} montages")
    stats = {"episode_candidates": len(promos), "montage_candidates": len(montage_promos)}
    promos += montage_promos
    stats["candidates_total"] = len(promos)

    # 4. validate + snap, 5. repair once
    valid, rejected = [], []
    first_try = repaired_ok = 0
    progress("validate", 0, len(promos), "Validating cut points")
    for i, p in enumerate(promos):
        errs = validate_promo(p, show, cfg)
        if not errs:
            first_try += 1
            valid.append(p)
        else:
            original = {"seg_refs": [asdict(r) for r in p.seg_refs], "errors": errs}
            try:
                S.repair(llm, show, p, errs, repair_model, gfb)
                errs2 = validate_promo(p, show, cfg)
            except Exception as e:  # noqa: BLE001
                errs2 = [f"repair LLM failed: {str(e)[:150]}"]
            if not errs2:
                p.checks["repaired"] = True
                p.checks["errors_before_repair"] = errs
                repaired_ok += 1
                valid.append(p)
            else:
                rejected.append({"id": p.id, "title": p.title, "source": p.source, "original": original,
                                 "after_repair": [asdict(r) for r in p.seg_refs], "errors": errs2})
        progress("validate", i + 1, len(promos))
    stats.update(first_try_pass=first_try, repaired_pass=repaired_ok)

    # 6. dedupe near-identical clips (keep the one the generator rated higher)
    valid, dupes = dedupe(valid, pc["dedupe_iou"], key=lambda p: p.self_avg())
    for p in valid:
        eps = p.episodes
        arcs = {S.arc_for(bible, e) for e in eps}
        p.arc = arcs.pop() if len(arcs) == 1 else "cross-arc"

    # 7. stranger judge
    progress("judge", 0, 1, f"Judging {len(valid)} promos as a stranger")
    S.judge(llm, valid, jud, fallback, pc["judge_batch_size"], pc["judge_temperature"], log=log)
    progress("judge", 1, 1, "Judging done")

    # 8. rank
    ranked = rank(valid, cfg["ranking"])

    # 9. reports
    meta = {"run_id": run_id, "show": show.title, "generator": gen, "judge": jud, "generator_fallback": gfb, "episodes": len(show.episodes),
            "episode_range": [show.episodes[0].number, show.episodes[-1].number], "k": k,
            "created": datetime.now().isoformat(timespec="seconds")}
    write_json(out / "promos.json", show.title, ranked, meta)
    write_csv(out / "promos.csv", ranked)
    write_md(out / "promos.md", show.title, ranked, meta)
    S.save_json(out / "rejected.json", rejected)
    S.save_json(out / "duplicates.json", [{"dropped": a.id, "kept": b.id, "iou": v} for a, b, v in dupes])
    write_quality(out / "quality_report.md", ranked, rejected, dupes, stats, meta)
    u = llm.usage
    summary = {**meta, "wall_seconds": round(time.time() - t_start, 1), "llm_calls": u.calls,
               "cached_calls": u.cached_calls, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
               "by_model": u.by_model, "llm_failures": u.failures, "failed_episodes": failed_eps,
               "list_price_cost_usd_if_all_tokens_on": llm.cost_summary(), "funnel": stats,
               "final_promos": len(ranked), "rejected": len(rejected), "duplicates": len(dupes),
               "parse_warnings": show.warnings}
    S.save_json(out / "run_summary.json", summary)
    log(f"Done in {summary['wall_seconds']}s: {len(ranked)} promos ({len(rejected)} rejected, {len(dupes)} dupes). "
        f"Calls={u.calls} (cached {u.cached_calls}), tokens in/out={u.input_tokens}/{u.output_tokens}")
    progress("done", 1, 1, str(out))
    return {"out_dir": str(out), "promos": ranked, "summary": summary, "bible": bible.model_dump()}
