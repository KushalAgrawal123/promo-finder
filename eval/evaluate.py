"""Evaluate a run against the gold set, and compare runs.

  python eval/evaluate.py --run ../outputs/<show>/<run_id>                 # gold recall + rule checks
  python eval/evaluate.py --run RUN_A --compare RUN_B [--episodes 1-15]     # run-vs-run agreement
  python eval/evaluate.py --run RUN --rating-template                       # CSV for blind human rating
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from promo_finder.parse import parse_time  # noqa: E402


def load_run(run: Path, episodes=None):
    data = json.loads((run / "promos.json").read_text())
    promos = data["promos"]
    if episodes:
        lo, hi = episodes
        promos = [p for p in promos if all(lo <= e <= hi for e in p["episodes"])]
    return data, promos


def seg_hits_gold(seg, g) -> bool:
    if seg["episode"] != g["episode"]:
        return False
    gs, ge, gk = parse_time(g["start"]), parse_time(g["end"]), parse_time(g["key"])
    if seg["start"] - 0.5 <= gk <= seg["end"] + 0.5:
        return True
    ov = max(0.0, min(seg["end"], ge) - max(seg["start"], gs))
    return ov >= 0.5 * min(ge - gs, seg["end"] - seg["start"])


def gold_eval(promos, gold, ks=(10, 20, 30)):
    out = {}
    for k in list(ks) + [len(promos)]:
        top = promos[:k]
        found = {g["id"]: next((p["rank"] for p in top if any(seg_hits_gold(s, g) for s in p["segments"])), None)
                 for g in gold}
        out[f"recall@{k if k != len(promos) else 'all'}"] = (sum(1 for v in found.values() if v), len(gold), found)
    return out


def iou(a, b):
    inter = sum(max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
                for x in a["segments"] for y in b["segments"] if x["episode"] == y["episode"])
    union = a["total_duration"] + b["total_duration"] - inter
    return inter / union if union else 0.0


def overlap_at_k(pa, pb, k, thr=0.3):
    A, B = pa[:k], pb[:k]
    matched = sum(1 for a in A if any(iou(a, b) >= thr for b in B))
    return matched, len(A)


def checks(promos):
    n = len(promos)
    return {
        "promos": n,
        "duration_20_60": sum(1 for p in promos if 20 <= p["total_duration"] <= 60),
        "hook_in_3s": sum(1 for p in promos if p["checks"].get("hook_in_3s")),
        "repaired": sum(1 for p in promos if p["checks"].get("repaired")),
        "montages": sum(1 for p in promos if p["source"] == "montage"),
        "mean_duration": round(sum(p["total_duration"] for p in promos) / max(n, 1), 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    default_gold = ROOT / "eval" / "gold.yaml"
    ap.add_argument("--gold", default=str(default_gold if default_gold.exists() else ROOT / "eval" / "gold.synthetic.yaml"))
    ap.add_argument("--compare")
    ap.add_argument("--episodes")
    ap.add_argument("--rating-template", action="store_true")
    ap.add_argument("--out", help="write the markdown report here")
    a = ap.parse_args()
    eps = tuple(int(x) for x in a.episodes.split("-")) if a.episodes else None
    run = Path(a.run)
    data, promos = load_run(run, eps)
    L = [f"# Evaluation — run `{data['meta']['run_id']}` ({data['meta']['generator']} → judge {data['meta']['judge']})", ""]
    if eps:
        L.append(f"Restricted to promos fully inside episodes {eps[0]}–{eps[1]}.\n")

    c = checks(promos)
    L += ["## Rule checks", f"- promos: {c['promos']} (montages: {c['montages']}), mean length {c['mean_duration']}s",
          f"- 20–60s: {c['duration_20_60']}/{c['promos']} · hook in first 3s: {c['hook_in_3s']}/{c['promos']} · "
          f"needed LLM repair: {c['repaired']}", ""]

    g = yaml.safe_load(Path(a.gold).read_text())
    gold = [m for m in g["moments"] if not eps or eps[0] <= m["episode"] <= eps[1]]
    if gold:
        ge = gold_eval(promos, gold)
        L += [f"## Gold recall (timestamps verified against scripts: {g.get('verified_against_scripts', False)}; "
              f"selection confirmed by a human editor: {g.get('human_reviewed', g.get('reviewed', False))})"]
        for key, (hit, tot, _) in ge.items():
            L.append(f"- {key}: **{hit}/{tot}** ({hit / tot:.0%})")
        found = ge[f"recall@all"][2]
        L += ["", "| gold | ep | moment | best rank |", "|---|---|---|---|"]
        for m in gold:
            r = found[m["id"]]
            L.append(f"| {m['id']} | {m['episode']} | {m['what']} | {r if r else '**missed**'} |")
        L.append("")

    if a.compare:
        dataB, promosB = load_run(Path(a.compare), eps)
        L += [f"## Agreement with run `{dataB['meta']['run_id']}` ({dataB['meta']['generator']})"]
        for k in (5, 10):
            m, n = overlap_at_k(promos, promosB, k)
            L.append(f"- top-{k}: {m}/{n} of run A's promos have a match (IoU ≥ 0.3) in run B's top-{k}")
        if gold:
            geB = gold_eval(promosB, gold)
            for key in ("recall@10", "recall@all"):
                L.append(f"- gold {key}: A {ge[key][0]}/{ge[key][1]} vs B {geB[key][0]}/{geB[key][1]}")
        cB = checks(promosB)
        L.append(f"- B rule checks: {cB}")
        L.append("")

    if a.rating_template:
        path = run / "human_rating_template.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["rank", "id", "segments", "title", "hook_1to5", "cliffhanger_1to5", "standalone_1to5",
                        "would_post_yes_no", "note"])
            for p in promos[:15]:
                segs = "; ".join(f"E{s['episode']:02d} {s['start_tc']}-{s['end_tc']}" for s in p["segments"])
                w.writerow([p["rank"], p["id"], segs, p["title"], "", "", "", "", ""])
        L.append(f"Rating template written to {path}")

    text = "\n".join(L)
    print(text)
    if a.out:
        Path(a.out).write_text(text)


if __name__ == "__main__":
    main()
