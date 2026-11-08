"""Validate + snap promo candidates (deterministic code — the LLM never writes timestamps).

For each candidate:
  1. beat IDs must exist, ranges must be in one episode and in order
  2. beat IDs -> times; never start or end in the middle of a spoken line
  3. hook rule: the hook beat must start within the first `hook_window_s` seconds (start is tightened)
  4. cliffhanger rule: the clip ends right after the cliffhanger beat (no answer/resolution after it)
  5. total duration must be within [min, max]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..parse import Beat, Show, fmt_time
from ..render import clip_transcript

EPS = 0.05


@dataclass
class SegRef:
    start_beat: str
    end_beat: str


@dataclass
class Promo:
    id: str
    source: str                      # "episode" | "montage"
    title: str
    seg_refs: list[SegRef]
    hook_beat: str
    cliffhanger_beat: str
    hook: str
    cliffhanger: str
    why_it_works: str
    tropes: list[str]
    self_scores: dict
    origin_episode: int | None = None
    # filled by validation
    segments: list[dict] = field(default_factory=list)   # {episode, start, end, duration, beat_ids}
    total_duration: float = 0.0
    checks: dict = field(default_factory=dict)
    transcript: str = ""
    arc: str = ""
    judge: dict | None = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    why_post_first: str = ""
    rank: int = 0
    post_first: bool = False

    @property
    def episodes(self) -> list[int]:
        out = []
        for s in self.segments:
            if s["episode"] not in out:
                out.append(s["episode"])
        return out

    def self_avg(self) -> float:
        v = [self.self_scores.get(k, 0) for k in ("hook", "cliffhanger", "standalone", "intensity")]
        return sum(v) / len(v)


def _snap_segment(show: Show, idx: dict[str, Beat], ref: SegRef):
    """Return (episode, start, end, in_range_beats) or raise ValueError."""
    a, b = idx.get(ref.start_beat), idx.get(ref.end_beat)
    if a is None or b is None:
        missing = [x for x, y in ((ref.start_beat, a), (ref.end_beat, b)) if y is None]
        raise ValueError(f"unknown beat id(s) {', '.join(missing)}")
    if a.episode != b.episode:
        raise ValueError(f"segment {ref.start_beat}..{ref.end_beat} spans two episodes; one segment = one episode")
    if b.idx < a.idx:
        a, b = b, a
    ep = show.episode(a.episode)
    beats = ep.beats[a.idx:b.idx + 1]
    start = a.start
    end = max(x.end for x in beats)
    # no mid-line cut at the start: a line (not selected) still being spoken -> start after it
    for x in ep.beats[:a.idx]:
        if x.kind == "dialogue" and x.start < start - EPS and x.end > start + 0.3:
            start = max(start, x.end)
    # no mid-line cut at the end: a line (not selected) starting before our end -> end before it
    last_sel_dialogue_end = max((x.end for x in beats if x.kind == "dialogue"), default=start)
    for x in ep.beats[b.idx + 1:]:
        if x.kind == "dialogue" and x.start < end - 0.3:
            end = max(min(end, x.start), last_sel_dialogue_end)
    return a.episode, start, end, beats


def validate_promo(p: Promo, show: Show, cfg: dict) -> list[str]:
    """Snap in place. Returns a list of errors (empty = valid)."""
    pc = cfg["pipeline"]
    idx = show.beat_index()
    errors: list[str] = []
    if not p.seg_refs:
        return ["no segments"]
    if len(p.seg_refs) > 4:
        errors.append(f"{len(p.seg_refs)} segments; max 4")
    snapped = []
    for r in p.seg_refs:
        try:
            snapped.append(list(_snap_segment(show, idx, r)))
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return errors

    checks = {"ids_valid": True, "trimmed_start_s": 0.0, "trimmed_end_s": 0.0}

    # ---- hook rule (first segment) ----
    hook = idx.get(p.hook_beat)
    ep0, s0, e0, beats0 = snapped[0]
    if hook is None or hook.episode != ep0 or not (beats0[0].idx <= hook.idx <= beats0[-1].idx):
        # fall back to the first story beat of the first segment and note it
        hook = next((x for x in beats0 if x.is_story), beats0[0])
        checks["hook_beat_fallback"] = True
        p.hook_beat = hook.id
    if hook.start - s0 > pc["hook_window_s"]:
        new_s0 = max(s0, hook.start - 0.3)
        checks["trimmed_start_s"] = round(new_s0 - s0, 2)
        snapped[0][1] = new_s0

    # ---- cliffhanger rule (last segment) ----
    cl = idx.get(p.cliffhanger_beat)
    epn, sn, en, beatsn = snapped[-1]
    if cl is None or cl.episode != epn or not (beatsn[0].idx <= cl.idx <= beatsn[-1].idx):
        cl = next((x for x in reversed(beatsn) if x.is_story), beatsn[-1])
        checks["cliffhanger_beat_fallback"] = True
        p.cliffhanger_beat = cl.id
    new_en = cl.end
    for x in beatsn:
        if x.start < cl.end - EPS and x.end > new_en:
            if x.kind == "dialogue":
                new_en = max(new_en, x.end)            # never cut a line mid-way
            else:
                new_en = max(new_en, min(x.end, cl.end + pc["max_tail_extension_s"]))
    if new_en < en - EPS:
        checks["trimmed_end_s"] = round(en - new_en, 2)
    snapped[-1][2] = new_en

    # ---- build segments ----
    segs, seg_beats, seg_starts = [], [], []
    for ep_n, s, e, beats in snapped:
        if e - s <= 0.5:
            errors.append(f"segment in E{ep_n} collapses to {e - s:.1f}s after snapping")
            continue
        inside = [x for x in beats if x.end > s + 0.1 and x.start < e - 0.1]
        segs.append({"episode": ep_n, "start": round(s, 2), "end": round(e, 2),
                     "start_tc": fmt_time(s), "end_tc": fmt_time(e), "duration": round(e - s, 2),
                     "beat_ids": [x.id for x in inside]})
        seg_beats.append(inside)
        seg_starts.append(s)
    if errors:
        return errors

    # segments must not overlap / go backwards inside the same episode
    for i in range(1, len(segs)):
        a, b = segs[i - 1], segs[i]
        if a["episode"] == b["episode"] and b["start"] < a["end"] - EPS:
            errors.append(f"segments {i} and {i + 1} overlap or are out of order in E{a['episode']}")

    total = sum(s["duration"] for s in segs)
    if total < pc["min_duration_s"] - EPS:
        errors.append(f"total {total:.1f}s < {pc['min_duration_s']}s")
    if total > pc["max_duration_s"] + EPS:
        errors.append(f"total {total:.1f}s > {pc['max_duration_s']}s")
    if errors:
        return errors

    # ---- hook-in-3s flag: first story beat of the clip starts within the window ----
    first_story = next((x for x in seg_beats[0] if x.is_story), None)
    checks["hook_in_3s"] = bool(first_story and max(0.0, first_story.start - segs[0]["start"]) <= pc["hook_window_s"]
                                and max(0.0, hook.start - segs[0]["start"]) <= pc["hook_window_s"])
    checks["duration_ok"] = True
    p.segments = segs
    p.total_duration = round(total, 2)
    p.checks.update(checks)
    p.transcript = clip_transcript(seg_beats, seg_starts, None)
    return []


def iou(a: Promo, b: Promo) -> float:
    """Temporal IoU over all segments (only same-episode time overlaps count)."""
    inter = 0.0
    for x in a.segments:
        for y in b.segments:
            if x["episode"] == y["episode"]:
                inter += max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
    union = a.total_duration + b.total_duration - inter
    return inter / union if union > 0 else 0.0


def dedupe(promos: list[Promo], threshold: float, key) -> tuple[list[Promo], list[tuple[Promo, Promo, float]]]:
    """Keep the better promo (by key) when two overlap more than threshold."""
    kept: list[Promo] = []
    dropped = []
    for p in sorted(promos, key=key, reverse=True):
        clash = next(((k, iou(p, k)) for k in kept if iou(p, k) > threshold), None)
        if clash:
            dropped.append((p, clash[0], round(clash[1], 2)))
        else:
            kept.append(p)
    return kept, dropped


def neighborhood(p: Promo, show: Show, pad: int = 15) -> list[Beat]:
    """Beats around a candidate, for the repair prompt."""
    idx = show.beat_index()
    out: dict[str, Beat] = {}
    for r in p.seg_refs:
        a, b = idx.get(r.start_beat), idx.get(r.end_beat)
        anchors = [x for x in (a, b) if x]
        if not anchors and p.origin_episode:
            ep = show.episode(p.origin_episode)
            anchors = [ep.beats[0], ep.beats[-1]]
        for x in anchors:
            ep = show.episode(x.episode)
            lo = max(0, min(y.idx for y in anchors if y.episode == x.episode) - pad)
            hi = min(len(ep.beats), max(y.idx for y in anchors if y.episode == x.episode) + pad + 1)
            for y in ep.beats[lo:hi]:
                out[y.id] = y
    return sorted(out.values(), key=lambda y: (y.episode, y.idx))
