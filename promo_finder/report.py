"""Write promos.json / promos.csv / promos.md / quality_report.md (code)."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import mean

from .steps.validate import Promo


def promo_dict(p: Promo) -> dict:
    d = asdict(p)
    d["seg_refs"] = [asdict(r) if not isinstance(r, dict) else r for r in p.seg_refs]
    d["episodes"] = p.episodes
    return d


def seg_str(p: Promo) -> str:
    return "; ".join(f"E{s['episode']:02d} {s['start_tc']}–{s['end_tc']}" for s in p.segments)


def write_json(path: Path, show_title: str, promos: list[Promo], meta: dict):
    path.write_text(json.dumps({"show": show_title, "meta": meta, "promos": [promo_dict(p) for p in promos]},
                               indent=2, ensure_ascii=False))


def write_csv(path: Path, promos: list[Promo]):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "post_first", "id", "score", "title", "segments", "duration_s", "hook", "cliffhanger",
                    "why_it_works", "why_post_first", "arc", "judge_hook_3s", "judge_cliffhanger",
                    "judge_standalone", "judge_intensity", "judge_confusions", "hook_in_3s", "repaired", "source"])
        for p in promos:
            j = p.judge or {}
            w.writerow([p.rank, "yes" if p.post_first else "", p.id, p.score, p.title, seg_str(p), p.total_duration,
                        p.hook, p.cliffhanger, p.why_it_works, p.why_post_first, p.arc, j.get("hook_3s"),
                        j.get("cliffhanger"), j.get("standalone"), j.get("intensity"),
                        " | ".join(j.get("confusions") or []), p.checks.get("hook_in_3s"), p.checks.get("repaired", False),
                        p.source])


def write_md(path: Path, show_title: str, promos: list[Promo], meta: dict, top_cards: int = 25):
    L = [f"# Promo Finder — {show_title}", "",
         f"Run `{meta.get('run_id')}` · generator `{meta.get('generator')}` · judge `{meta.get('judge')}` · "
         f"{len(promos)} validated promos from {meta.get('episodes')} episodes.", "",
         "Timestamps are episode-relative, in the screenplay's own `MM:SS.ss` format. "
         "Scores are 1–10 (judge = a model that saw ONLY the clip text, like a stranger scrolling).", "",
         "## Post these first", "",
         "| # | Promo | Segments | Len | Score | Why post first |", "|---|---|---|---|---|---|"]
    for p in [x for x in promos if x.post_first]:
        L.append(f"| {p.rank} | **{p.title}** | {seg_str(p)} | {p.total_duration:.0f}s | {p.score} | {p.why_post_first} |")
    L += ["", f"## Top {min(top_cards, len(promos))} promos (full list in promos.csv / promos.json)", ""]
    for p in promos[:top_cards]:
        j = p.judge or {}
        L += [f"### #{p.rank} — {p.title}  ·  score {p.score}{'  ·  ⭐ post first' if p.post_first else ''}",
              f"- **Segments:** {seg_str(p)}  (total {p.total_duration:.1f}s, {p.source})",
              f"- **Hook (0–3s):** {p.hook}",
              f"- **Cliffhanger:** {p.cliffhanger}",
              f"- **Why it works:** {p.why_it_works}",
              f"- **Arc / tropes:** {p.arc} / {', '.join(p.tropes)}",
              f"- **Stranger judge:** hook {j.get('hook_3s')} · cliffhanger {j.get('cliffhanger')} · standalone "
              f"{j.get('standalone')} · intensity {j.get('intensity')} — _{j.get('verdict', 'n/a')}_",
              ]
        if j.get("confusions"):
            L.append(f"- **Confusing without context:** {'; '.join(j['confusions'])}")
        if p.checks.get("trimmed_start_s") or p.checks.get("trimmed_end_s") or p.checks.get("repaired"):
            L.append(f"- **Auto-fixes:** start tightened {p.checks.get('trimmed_start_s', 0)}s, end trimmed "
                     f"{p.checks.get('trimmed_end_s', 0)}s{', repaired by LLM' if p.checks.get('repaired') else ''}")
        L += ["", "<details><summary>Clip transcript</summary>", "", "```", p.transcript, "```", "</details>", ""]
    path.write_text("\n".join(L))


def write_quality(path: Path, promos: list[Promo], rejected: list[dict], dupes: list, stats: dict, meta: dict):
    n = len(promos)
    total = stats["candidates_total"]
    durs = [p.total_duration for p in promos]
    bins = Counter(min(5, int((d - 20) // 8)) for d in durs) if durs else Counter()
    labels = ["20–28s", "28–36s", "36–44s", "44–52s", "52–60s", "60s"]
    judged = [p for p in promos if p.judge]

    def avg(key):
        v = [p.judge[key] for p in judged]
        return f"{mean(v):.2f}" if v else "n/a"

    agree = []
    for p in judged:
        s = p.self_scores
        agree.append(abs(mean([s['hook'], s['cliffhanger'], s['standalone'], s['intensity']]) -
                         mean([p.judge[k] for k in ('hook_3s', 'cliffhanger', 'standalone', 'intensity')])))
    reasons = Counter()
    for r in rejected:
        for e in r["errors"]:
            key = ("duration too long" if ">" in e and "s" in e and "total" in e else
                   "duration too short" if "<" in e and "total" in e else
                   "unknown beat id" if "unknown beat" in e else
                   "LLM error" if "LLM" in e or "failed" in e else e[:60])
            reasons[key] += 1
    L = ["# Quality report", "",
         f"Run `{meta.get('run_id')}` — generator `{meta.get('generator')}`, judge `{meta.get('judge')}`", "",
         "## Pipeline funnel",
         f"- Candidates proposed by the generator: **{total}** ({stats['episode_candidates']} single-episode + "
         f"{stats['montage_candidates']} montages)",
         f"- Passed all rules on the first try: **{stats['first_try_pass']}** ({stats['first_try_pass'] / max(total, 1):.0%})",
         f"- Fixed by one LLM repair round: **{stats['repaired_pass']}**",
         f"- Rejected (still failing after repair): **{len(rejected)}**",
         f"- Removed as near-duplicates (IoU > threshold): **{len(dupes)}**",
         f"- **Final validated promos: {n}**", "",
         "## Rule checks on final promos (enforced in code)",
         f"- Duration within 20–60s: {sum(1 for d in durs if 20 <= d <= 60)}/{n}",
         f"- Hook beat inside first 3s: {sum(1 for p in promos if p.checks.get('hook_in_3s'))}/{n}",
         f"- Start auto-tightened onto the hook: {sum(1 for p in promos if p.checks.get('trimmed_start_s'))}",
         f"- End auto-trimmed to the cliffhanger: {sum(1 for p in promos if p.checks.get('trimmed_end_s'))}",
         f"- Hook/cliffhanger beat ID invalid → fallback used: "
         f"{sum(1 for p in promos if p.checks.get('hook_beat_fallback') or p.checks.get('cliffhanger_beat_fallback'))}",
         "", "## Duration distribution"]
    for i, lab in enumerate(labels[:5]):
        L.append(f"- {lab}: {'█' * bins.get(i, 0)} {bins.get(i, 0)}")
    L += ["", "## Stranger-judge scores (1–10, mean over final promos)",
          f"- Judged: {len(judged)}/{n}",
          f"- hook_3s {avg('hook_3s')} · cliffhanger {avg('cliffhanger')} · standalone {avg('standalone')} · "
          f"intensity {avg('intensity')}",
          f"- Would keep watching: {sum(1 for p in judged if p.judge['would_keep_watching'])}/{len(judged)}",
          f"- Promos with ≥1 'confusing without context' note: {sum(1 for p in judged if p.judge['confusions'])}",
          f"- Generator self-score vs judge: mean absolute gap {mean(agree):.2f} points"
          f" (generator {'over' if judged and mean(mean(p.self_scores.values()) for p in judged) > mean(mean(p.judge[k] for k in ('hook_3s','cliffhanger','standalone','intensity')) for p in judged) else 'under'}-rates itself)"
          if agree else "- n/a",
          "", "## Reject reasons"]
    for k, v in reasons.most_common():
        L.append(f"- {k}: {v}")
    L += ["", "## Lowest standalone scores (where context is missing)"]
    for p in sorted(judged, key=lambda p: p.judge["standalone"])[:5]:
        L.append(f"- {p.id} '{p.title}' standalone {p.judge['standalone']}: {'; '.join(p.judge['confusions']) or p.judge['verdict']}")
    path.write_text("\n".join(L))
