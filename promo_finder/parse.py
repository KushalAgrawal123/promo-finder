"""Screenplay parser: turns Kuku-style microdrama screenplays into structured beats.

Deterministic code, no AI. Tolerant of header case changes and small format drift;
problems become warnings, not crashes, so a new show still runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

NON_DIALOGUE_KINDS = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx", "SFX": "sfx", "SOUND": "sfx"}

RE_EP_NUM = re.compile(r"^EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)", re.I)
RE_LOGLINE = re.compile(r"^EPISODE LOGLINE:\s*(.*)$", re.I)
RE_CONT = re.compile(r"^CONTINUITY NOTES:\s*(.*)$", re.I)
RE_SCENE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
RE_SCENE_HEAD = re.compile(r"^SCENE HEADING:\s*(.*)$", re.I)
RE_SCENE_START = re.compile(r"^SCENE START:\s*(\S+)", re.I)
RE_SCENE_END = re.compile(r"^SCENE END:\s*(\S+)", re.I)
RE_LABEL = re.compile(r"^\*\*(.+?):\*\*\s*$")
RE_TIMES = re.compile(r"^Start:\s*\(?\s*([\d:.]+)\s*\)?\s*\|\s*End:\s*\(?\s*([\d:.]+)\s*\)?", re.I)
RE_FILE_EP = re.compile(r"episode[-_ ]?0*(\d+)", re.I)


def parse_time(s: str) -> float:
    """'01:13.10' -> 73.1 ; '1:02:03.5' -> 3723.5 ; '45.2' -> 45.2"""
    s = s.strip().strip("()")
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"bad timestamp {s!r}") from e
    secs = 0.0
    for n in nums:
        secs = secs * 60 + n
    return round(secs, 3)


def fmt_time(secs: float) -> str:
    """73.1 -> '01:13.10' (same format as the input screenplays)."""
    secs = max(0.0, secs)
    m = int(secs // 60)
    s = secs - m * 60
    return f"{m:02d}:{s:05.2f}"


@dataclass
class Beat:
    id: str
    episode: int
    scene: int
    idx: int
    kind: str          # dialogue | action | camera | sfx
    speaker: str       # "" for non-dialogue
    cues: str          # e.g. "Cold, Commanding" / "O.S."
    text: str
    start: float
    end: float

    @property
    def is_story(self) -> bool:
        """Dialogue/action/sfx carry story; camera lines are visual description."""
        return self.kind != "camera"


@dataclass
class Scene:
    number: int
    heading: str
    start: float | None
    end: float | None


@dataclass
class Episode:
    number: int
    logline: str
    continuity: str
    source: str
    scenes: list[Scene] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [s.end for s in self.scenes if s.end] + [b.end for b in self.beats]
        return max(ends) if ends else 0.0

    @property
    def speakers(self) -> list[str]:
        seen: dict[str, None] = {}
        for b in self.beats:
            if b.kind == "dialogue":
                seen.setdefault(b.speaker, None)
        return list(seen)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        return d


@dataclass
class Show:
    title: str
    episodes: list[Episode]

    def beat_index(self) -> dict[str, Beat]:
        return {b.id: b for e in self.episodes for b in e.beats}

    def episode(self, n: int) -> Episode:
        for e in self.episodes:
            if e.number == n:
                return e
        raise KeyError(n)

    @property
    def warnings(self) -> list[str]:
        return [f"E{e.number}: {w}" for e in self.episodes for w in e.warnings]


def _split_label(label: str) -> tuple[str, str]:
    """'MIRA (Confused, Breathless)' -> ('MIRA', 'Confused, Breathless')
    'LAWYER (O.S.) (Firm)' -> ('LAWYER', 'O.S.; Firm')"""
    name = label.split("(")[0].strip()
    cues = re.findall(r"\(([^)]*)\)", label)
    return name, "; ".join(c.strip() for c in cues)


def parse_episode_text(text: str, source: str = "", fallback_number: int | None = None) -> Episode:
    lines = text.splitlines()
    ep = Episode(number=fallback_number or 0, logline="", continuity="", source=source)
    scene_no = 0
    beat_no = 0
    pending_label: str | None = None
    pending_times: tuple[float, float] | None = None
    pending_text: list[str] = []

    def flush():
        nonlocal pending_label, pending_times, pending_text, beat_no
        if pending_label is None:
            return
        if pending_times is None:
            ep.warnings.append(f"block '{pending_label}' has no timestamps; skipped")
        else:
            up = pending_label.upper().strip()
            kind = NON_DIALOGUE_KINDS.get(up)
            if kind:
                speaker, cues = "", ""
            else:
                kind = "dialogue"
                speaker, cues = _split_label(pending_label)
            body = " ".join(t.strip() for t in pending_text).strip()
            if kind == "dialogue":
                body = body.strip('"').strip("“”")
            s, e = pending_times
            if e < s:
                ep.warnings.append(f"beat end < start at {fmt_time(s)}; swapped")
                s, e = e, s
            beat_no += 1
            ep.beats.append(Beat(
                id=f"E{ep.number:02d}-B{beat_no:03d}", episode=ep.number, scene=max(scene_no, 1),
                idx=beat_no - 1, kind=kind, speaker=speaker, cues=cues, text=body, start=s, end=e))
        pending_label, pending_times, pending_text = None, None, []

    for raw in lines:
        line = raw.strip()
        if m := RE_EP_NUM.match(line):
            ep.number = int(m.group(1))
            continue
        if m := RE_LOGLINE.match(line):
            ep.logline = m.group(1).strip()
            continue
        if m := RE_CONT.match(line):
            ep.continuity = m.group(1).strip()
            continue
        if m := RE_SCENE.match(line):
            flush()
            scene_no = int(m.group(1))
            ep.scenes.append(Scene(scene_no, "", None, None))
            continue
        if ep.scenes and (m := RE_SCENE_HEAD.match(line)):
            ep.scenes[-1].heading = m.group(1).strip()
            continue
        if ep.scenes and (m := RE_SCENE_START.match(line)):
            try:
                ep.scenes[-1].start = parse_time(m.group(1))
            except ValueError as e:
                ep.warnings.append(str(e))
            continue
        if ep.scenes and (m := RE_SCENE_END.match(line)):
            try:
                ep.scenes[-1].end = parse_time(m.group(1))
            except ValueError as e:
                ep.warnings.append(str(e))
            continue
        if m := RE_LABEL.match(line):
            flush()
            pending_label = m.group(1).strip()
            continue
        if pending_label is not None:
            if pending_times is None and (m := RE_TIMES.match(line)):
                try:
                    pending_times = (parse_time(m.group(1)), parse_time(m.group(2)))
                except ValueError as e:
                    ep.warnings.append(str(e))
                continue
            if line.startswith("---"):
                flush()
                continue
            if line:
                pending_text.append(line)
            elif pending_text:
                flush()
    flush()

    # beat IDs were created before the episode number may have been known; fix them
    for i, b in enumerate(ep.beats):
        b.episode = ep.number
        b.id = f"E{ep.number:02d}-B{i + 1:03d}"
    if not ep.beats:
        ep.warnings.append("no timestamped beats found — check format")
    return ep


def parse_episode_file(path: str | Path) -> Episode:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    m = RE_FILE_EP.search(path.stem)
    return parse_episode_text(text, source=path.name, fallback_number=int(m.group(1)) if m else None)


def parse_show(folder: str | Path, title: str | None = None) -> Show:
    folder = Path(folder)
    files = sorted(p for p in folder.glob("*.txt") if not p.name.startswith("."))
    if not files:
        raise FileNotFoundError(f"no .txt screenplay files in {folder}")
    eps = [parse_episode_file(p) for p in files]
    # files without a number get the next free number in sorted order
    used = {e.number for e in eps if e.number}
    nxt = 1
    for e in eps:
        if not e.number:
            while nxt in used:
                nxt += 1
            e.number = nxt
            used.add(nxt)
            for i, b in enumerate(e.beats):
                b.episode = nxt
                b.id = f"E{nxt:02d}-B{i + 1:03d}"
    eps.sort(key=lambda e: e.number)
    return Show(title=title or folder.name, episodes=eps)


def parse_texts(named_texts: list[tuple[str, str]], title: str = "uploaded show") -> Show:
    """For the Streamlit uploader: [(filename, text), ...]"""
    eps = []
    for name, text in named_texts:
        m = RE_FILE_EP.search(Path(name).stem)
        eps.append(parse_episode_text(text, source=name, fallback_number=int(m.group(1)) if m else None))
    eps.sort(key=lambda e: e.number)
    return Show(title=title, episodes=eps)
