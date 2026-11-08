"""Score + rank (deterministic code). Weights live in config.yaml."""
from __future__ import annotations

from .validate import Promo

DIMS = ("hook_3s", "cliffhanger", "standalone", "intensity")


def score_promo(p: Promo, rcfg: dict) -> float:
    w = rcfg["weights"]
    if p.judge:
        vals = {d: float(p.judge[d]) for d in DIMS}
        src = "judge"
    else:  # judge unavailable: fall back to the generator's self-scores, with a penalty
        s = p.self_scores
        vals = {"hook_3s": s["hook"], "cliffhanger": s["cliffhanger"], "standalone": s["standalone"],
                "intensity": s["intensity"]}
        src = "self (judge missing)"
    base = sum(w[d] * vals[d] for d in DIMS) / sum(w.values())
    bd = {"base": round(base, 2), "scores_from": src}
    pen = 0.0
    if not p.checks.get("hook_in_3s"):
        pen += rcfg["penalty_no_hook_in_3s"]; bd["no_hook_in_3s"] = -rcfg["penalty_no_hook_in_3s"]
    if p.checks.get("repaired"):
        pen += rcfg["penalty_repaired"]; bd["repaired"] = -rcfg["penalty_repaired"]
    if p.judge:
        n = min(len(p.judge.get("confusions") or []), rcfg["max_confusion_penalty_items"])
        if n:
            pen += n * rcfg["penalty_per_confusion"]; bd["confusions"] = -n * rcfg["penalty_per_confusion"]
        if p.judge.get("would_keep_watching") is False:
            pen += rcfg["penalty_would_not_watch"]; bd["would_not_watch"] = -rcfg["penalty_would_not_watch"]
    else:
        pen += 1.0; bd["judge_missing"] = -1.0
    bonus = 0.0
    if p.episodes and max(p.episodes) <= rcfg["funnel_last_episode"]:
        bonus = rcfg["funnel_bonus"]; bd["funnel_bonus"] = bonus
    p.score = round(base - pen + bonus, 2)
    p.score_breakdown = bd
    return p.score


def _why_post_first(p: Promo) -> str:
    j = p.judge or {}
    strengths = sorted(((j.get(d, 0), d) for d in DIMS), reverse=True)[:2]
    names = {"hook_3s": "instant hook", "cliffhanger": "brutal cliffhanger", "standalone": "clear without context",
             "intensity": "high emotional punch"}
    parts = [f"{names[d]} ({int(v)}/10)" for v, d in strengths if v]
    if p.score_breakdown.get("funnel_bonus"):
        parts.append(f"from Ep {', '.join(map(str, p.episodes))} so new viewers hit the payoff early")
    if p.arc:
        parts.append(f"tests the '{p.arc}' arc")
    if p.tropes:
        parts.append(f"trope: {p.tropes[0]}")
    return "; ".join(parts)


def rank(promos: list[Promo], rcfg: dict) -> list[Promo]:
    for p in promos:
        score_promo(p, rcfg)
    ordered = sorted(promos, key=lambda p: (p.score, p.self_avg()), reverse=True)
    for i, p in enumerate(ordered, 1):
        p.rank = i
    # diversity-aware "post first" list: <= N per arc, no shared cliffhanger beat
    per_arc: dict[str, int] = {}
    used_cliffs: set[str] = set()
    chosen = 0
    for p in ordered:
        if chosen >= rcfg["post_first_count"]:
            break
        arc = p.arc or "?"
        if per_arc.get(arc, 0) >= rcfg["max_per_arc_in_post_first"] or p.cliffhanger_beat in used_cliffs:
            continue
        p.post_first = True
        p.why_post_first = _why_post_first(p)
        per_arc[arc] = per_arc.get(arc, 0) + 1
        used_cliffs.add(p.cliffhanger_beat)
        chosen += 1
    return ordered
