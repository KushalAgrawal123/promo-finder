"""Pydantic schemas for every LLM output. The LLM references beats by ID only;
timestamps are always computed by code from the parsed screenplay."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- series bible ----------
class Character(_Strict):
    name: str
    aliases: list[str] = Field(description="Every speaker label that refers to this character")
    role: str


class Arc(_Strict):
    name: str
    first_episode: int
    last_episode: int
    summary: str


class Bible(_Strict):
    premise: str
    characters: list[Character]
    arcs: list[Arc]
    tropes: list[str]
    uncertain_aliases: list[str]


# ---------- per-episode candidates ----------
class SegmentRef(_Strict):
    start_beat: str = Field(description="Beat ID where the segment starts, e.g. E03-B012")
    end_beat: str = Field(description="Beat ID where the segment ends (inclusive)")


class SelfScores(_Strict):
    hook: int
    cliffhanger: int
    standalone: int
    intensity: int


class Candidate(_Strict):
    title: str
    segments: list[SegmentRef]
    hook_beat: str
    cliffhanger_beat: str
    hook: str = Field(description="What happens in the first 3 seconds")
    cliffhanger: str = Field(description="The unresolved moment the clip ends on")
    why_it_works: str = Field(description="One line")
    tropes: list[str]
    self_scores: SelfScores


class KeyMoment(_Strict):
    start_beat: str
    end_beat: str
    kind: Literal["hook", "cliffhanger", "reveal", "confrontation", "romance", "action"]
    description: str
    intensity: int


class EpisodeResult(_Strict):
    candidates: list[Candidate]
    key_moments: list[KeyMoment]


# ---------- montages ----------
class MontageCandidate(_Strict):
    title: str
    moment_ids: list[str] = Field(description="2-4 moment IDs in playback order")
    hook: str
    cliffhanger: str
    why_it_works: str
    tropes: list[str]
    self_scores: SelfScores


class MontageResult(_Strict):
    montages: list[MontageCandidate]


# ---------- repair ----------
class RepairResult(_Strict):
    segments: list[SegmentRef]
    hook_beat: str
    cliffhanger_beat: str


# ---------- judge ----------
class Verdict(_Strict):
    promo_id: str
    hook_3s: int
    cliffhanger: int
    standalone: int
    intensity: int
    confusions: list[str]
    would_keep_watching: bool
    verdict: str


class JudgeResult(_Strict):
    verdicts: list[Verdict]
