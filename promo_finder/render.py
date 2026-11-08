"""Render parsed beats into compact text for prompts and reports (code, no AI)."""
from __future__ import annotations

from .parse import Beat, Episode, fmt_time

CAM_WORDS = 18


def _short(text: str, n: int) -> str:
    w = text.split()
    return text if len(w) <= n else " ".join(w[:n]) + " …"


def label(b: Beat, aliases: dict[str, str] | None = None) -> str:
    if b.kind == "dialogue":
        sp = b.speaker
        if aliases and sp.upper() in aliases and aliases[sp.upper()].upper() != sp.upper():
            sp = f"{sp} [={aliases[sp.upper()]}]"
        cue = f" ({b.cues})" if b.cues else ""
        return f'{sp}{cue}: "{b.text}"'
    if b.kind == "camera":
        return f"CAM: {_short(b.text, CAM_WORDS)}"
    if b.kind == "sfx":
        return f"SFX: {b.text}"
    return f"ACTION: {b.text}"


def episode_transcript(ep: Episode, aliases: dict[str, str] | None = None) -> str:
    """One beat per line: BEAT_ID [start] TYPE: text — the LLM refers to beats by ID only."""
    out = []
    scene = None
    for b in ep.beats:
        if b.scene != scene:
            scene = b.scene
            sc = next((s for s in ep.scenes if s.number == scene), None)
            if sc and sc.heading:
                out.append(f"# SCENE {scene}: {sc.heading}")
        out.append(f"{b.id} [{fmt_time(b.start)}] {label(b, aliases)}")
    return "\n".join(out)


def beats_with_durations(beats: list[Beat], aliases: dict[str, str] | None = None) -> str:
    """For the repair prompt: exact start-end and length of each beat."""
    return "\n".join(f"{b.id} [{fmt_time(b.start)}-{fmt_time(b.end)} | {b.end - b.start:.1f}s] {label(b, aliases)}"
                     for b in beats)


def clip_transcript(segments_beats: list[list[Beat]], seg_starts: list[float],
                    aliases: dict[str, str] | None = None) -> str:
    """What a viewer sees: beats inside the clip, times relative to clip start (0:00)."""
    out = []
    offset = 0.0
    for i, (beats, s0) in enumerate(zip(segments_beats, seg_starts)):
        if i:
            out.append("--- CUT ---")
        last_end = s0
        for b in beats:
            rel = offset + max(0.0, b.start - s0)
            out.append(f"[{int(rel // 60)}:{rel % 60:04.1f}] {label(b, aliases)}")
            last_end = max(last_end, b.end)
        offset += max(0.0, last_end - s0) if beats else 0.0
    return "\n".join(out)
