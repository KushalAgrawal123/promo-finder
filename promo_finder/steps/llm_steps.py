"""The AI steps: series bible, per-episode candidates, cross-episode montages, repair, judge."""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import LLM
from ..parse import Show, Episode, fmt_time
from ..render import episode_transcript, beats_with_durations
from ..schemas import Bible, EpisodeResult, MontageResult, RepairResult, JudgeResult
from .validate import Promo, SegRef, neighborhood

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    text = (PROMPT_DIR / f"{name}.md").read_text()
    sys_part, user_part = text.split("## USER", 1)
    return sys_part.replace("## SYSTEM", "").strip(), user_part.strip()


def fill(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ---------------- bible ----------------
def build_bible(llm: LLM, show: Show, model: str, fallback: str | None = None) -> Bible:
    system, user = load_prompt("bible")
    lines = []
    for e in show.episodes:
        lines.append(f"Episode {e.number}: {e.logline}\n  Continuity: {e.continuity}\n"
                     f"  Speaker labels: {', '.join(e.speakers)}")
    return llm.complete_json("bible", "bible", system,
                             fill(user, title=show.title, episode_index="\n".join(lines)),
                             Bible, model, temperature=0.2, fallback=fallback)


def alias_map(bible: Bible) -> dict[str, str]:
    m = {}
    for c in bible.characters:
        for a in c.aliases + [c.name]:
            m[a.upper().strip()] = c.name
    return m


def bible_text(bible: Bible) -> str:
    chars = "\n".join(f"- {c.name} ({c.role}); labels: {', '.join(c.aliases)}" for c in bible.characters)
    arcs = "\n".join(f"- Ep {a.first_episode}-{a.last_episode}: {a.name} — {a.summary}" for a in bible.arcs)
    return f"Premise: {bible.premise}\nCharacters:\n{chars}\nArcs:\n{arcs}\nTropes: {', '.join(bible.tropes)}"


def arc_for(bible: Bible | None, episode: int) -> str:
    if not bible:
        return ""
    for a in bible.arcs:
        if a.first_episode <= episode <= a.last_episode:
            return a.name
    return ""


# ---------------- candidates ----------------
def episode_candidates(llm: LLM, show: Show, eps: list[Episode], bible: Bible, model: str, k: int,
                       temperature: float, fallback: str | None = None):
    """One LLM call for a window of 1+ consecutive episodes (fewer calls = fits free-tier daily quotas)."""
    system, user = load_prompt("candidates")
    first = eps[0]
    prev = next((e for e in show.episodes if e.number == first.number - 1), None)
    aliases = alias_map(bible)
    blocks = [f"===== EPISODE {e.number} (length {fmt_time(e.duration)}) =====\n{episode_transcript(e, aliases)}"
              for e in eps]
    prompt = fill(user, bible=bible_text(bible), prev_n=prev.number if prev else "-",
                  prev_logline=prev.logline if prev else "(this is the first episode)",
                  n_eps=len(eps), transcript="\n\n".join(blocks), k=k)
    key = f"E{first.number:02d}" if len(eps) == 1 else f"E{first.number:02d}-E{eps[-1].number:02d}"
    res: EpisodeResult = llm.complete_json("candidates", key, system, prompt, EpisodeResult,
                                           model, temperature=temperature, fallback=fallback)
    wanted = {e.number for e in eps}
    per_ep: dict[int, int] = {}
    promos = []
    for c in res.candidates:
        sb = c.segments[0].start_beat.strip() if c.segments else ""
        try:
            ep_n = int(sb.split("-")[0].lstrip("Ee"))
        except ValueError:
            ep_n = first.number
        if ep_n not in wanted or per_ep.get(ep_n, 0) >= k:
            continue
        per_ep[ep_n] = per_ep.get(ep_n, 0) + 1
        promos.append(Promo(
            id=f"E{ep_n:02d}-P{per_ep[ep_n]}", source="episode", title=c.title,
            seg_refs=[SegRef(s.start_beat.strip(), s.end_beat.strip()) for s in c.segments],
            hook_beat=c.hook_beat.strip(), cliffhanger_beat=c.cliffhanger_beat.strip(), hook=c.hook,
            cliffhanger=c.cliffhanger, why_it_works=c.why_it_works, tropes=c.tropes,
            self_scores=c.self_scores.model_dump(), origin_episode=ep_n))
    moments = []
    for km in res.key_moments:
        try:
            ep_n = int(km.start_beat.strip().split("-")[0].lstrip("Ee"))
        except ValueError:
            continue
        if ep_n in wanted:
            moments.append((ep_n, km))
    return promos, moments


# ---------------- montages ----------------
def build_montages(llm: LLM, show: Show, bible: Bible, moments: list[dict], model: str, m: int,
                   temperature: float, fallback: str | None = None) -> list[Promo]:
    """moments: [{id, episode, start_beat, end_beat, kind, intensity, description, duration}]"""
    if len(moments) < 2:
        return []
    system, user = load_prompt("montage")
    by_id = {x["id"]: x for x in moments}
    lines = [f"{x['id']} | E{x['episode']} | {arc_for(bible, x['episode'])} | {x['duration']:.1f}s | {x['kind']} | "
             f"{x['intensity']} | {x['description']}" for x in moments]
    res: MontageResult = llm.complete_json("montage", "montage", system,
                                           fill(user, bible=bible_text(bible), moments="\n".join(lines), m=m),
                                           MontageResult, model, temperature=temperature, fallback=fallback)
    idx = show.beat_index()
    promos = []
    for i, c in enumerate(res.montages[:m]):
        ms = [by_id[x.strip()] for x in c.moment_ids if x.strip() in by_id]
        if len(ms) < 2:
            continue
        refs = [SegRef(x["start_beat"], x["end_beat"]) for x in ms]
        first_beats = show.episode(ms[0]["episode"]).beats[idx[ms[0]["start_beat"]].idx: idx[ms[0]["end_beat"]].idx + 1]
        last_beats = show.episode(ms[-1]["episode"]).beats[idx[ms[-1]["start_beat"]].idx: idx[ms[-1]["end_beat"]].idx + 1]
        hook_b = next((b for b in first_beats if b.is_story), first_beats[0])
        cl_b = next((b for b in reversed(last_beats) if b.is_story), last_beats[-1])
        promos.append(Promo(
            id=f"M{i + 1:02d}", source="montage", title=c.title, seg_refs=refs, hook_beat=hook_b.id,
            cliffhanger_beat=cl_b.id, hook=c.hook, cliffhanger=c.cliffhanger, why_it_works=c.why_it_works,
            tropes=c.tropes, self_scores=c.self_scores.model_dump()))
    return promos


# ---------------- repair ----------------
def repair(llm: LLM, show: Show, p: Promo, errors: list[str], model: str, fallback: str | None = None) -> Promo:
    system, user = load_prompt("repair")
    beats = neighborhood(p, show)
    segs = "; ".join(f"{r.start_beat}..{r.end_beat}" for r in p.seg_refs)
    res: RepairResult = llm.complete_json(
        "repair", p.id, system,
        fill(user, title=p.title, hook=p.hook, cliffhanger=p.cliffhanger, errors="\n".join(f"- {e}" for e in errors),
             segments=segs, beats=beats_with_durations(beats)),
        RepairResult, model, temperature=0.2, fallback=fallback)
    p.seg_refs = [SegRef(s.start_beat.strip(), s.end_beat.strip()) for s in res.segments]
    p.hook_beat, p.cliffhanger_beat = res.hook_beat.strip(), res.cliffhanger_beat.strip()
    return p


# ---------------- judge ----------------
def judge(llm: LLM, promos: list[Promo], model: str, fallback: str | None, batch: int, temperature: float, log=print):
    system, user = load_prompt("judge")
    for i in range(0, len(promos), batch):
        chunk = promos[i:i + batch]
        clips = "\n\n".join(f"=== promo_id: {p.id} (length {p.total_duration:.0f}s) ===\n{p.transcript}" for p in chunk)
        key = f"batch{i // batch + 1:02d}__" + "_".join(p.id for p in chunk)
        res = None
        for m in [model] + ([fallback] if fallback and fallback != model else []):
            try:
                res = llm.complete_json("judge", key, system, fill(user, clips=clips), JudgeResult, m,
                                        temperature=temperature)
                judge_model = m
                break
            except Exception as e:  # noqa: BLE001
                log(f"  judge {m} failed on {key}: {str(e)[:150]}")
        if res is None:
            continue
        got = {v.promo_id.strip(): v for v in res.verdicts}
        for p in chunk:
            v = got.get(p.id)
            if v:
                d = v.model_dump()
                d["model"] = judge_model
                p.judge = d
    return promos


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
