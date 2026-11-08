"""CLI: python -m promo_finder run --input <folder of .txt screenplays> --out <dir>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    ap = argparse.ArgumentParser(prog="promo_finder", description="Find ranked promo clips in microdrama screenplays")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the full pipeline on a show folder")
    r.add_argument("--input", required=True, help="folder with one .txt screenplay per episode")
    r.add_argument("--out", default=str(root.parent / "outputs"), help="output root folder")
    r.add_argument("--title", help="show title (default: folder name)")
    r.add_argument("--model", help="generator model, provider/model (e.g. gemini/gemini-3.5-flash)")
    r.add_argument("--judge-model", help="judge model, provider/model (e.g. groq/openai/gpt-oss-120b)")
    r.add_argument("--episodes", help="range like 1-10")
    r.add_argument("--k", type=int, help="max candidates per episode")
    r.add_argument("--montages", type=int, help="max cross-episode montages (0 = off)")
    r.add_argument("--episodes-per-call", type=int, help="episodes per generator call (1 = one call per episode)")
    r.add_argument("--run-id", help="name of the run folder")
    r.add_argument("--config", help="path to config.yaml")
    r.add_argument("--no-cache", action="store_true", help="ignore cached LLM responses")
    p = sub.add_parser("parse", help="parse only and print stats (no API calls)")
    p.add_argument("--input", required=True)
    a = ap.parse_args(argv)

    from .parse import parse_show
    if a.cmd == "parse":
        show = parse_show(a.input)
        for e in show.episodes:
            print(f"E{e.number:02d}  scenes={len(e.scenes)}  beats={len(e.beats)}  duration={e.duration:.1f}s  "
                  f"warnings={len(e.warnings)}")
        print(f"total beats: {sum(len(e.beats) for e in show.episodes)}; warnings: {show.warnings}")
        return 0

    from .pipeline import load_config, run
    show = parse_show(a.input, title=a.title)
    eps = tuple(int(x) for x in a.episodes.split("-")) if a.episodes else None
    if eps and len(eps) == 1:
        eps = (eps[0], eps[0])
    cfg = load_config(a.config)
    if a.episodes_per_call:
        cfg["pipeline"]["episodes_per_call"] = a.episodes_per_call
    res = run(show, a.out, cfg=cfg, generator=a.model, judge_model=a.judge_model, episodes=eps,
              k=a.k, use_cache=not a.no_cache, run_id=a.run_id, montages=a.montages)
    print(f"\nOutputs: {res['out_dir']}")
    for p in res["promos"][:10]:
        segs = "; ".join(f"E{s['episode']:02d} {s['start_tc']}-{s['end_tc']}" for s in p.segments)
        print(f"#{p.rank:<3} {p.score:>5}  {p.total_duration:>4.0f}s  {segs:<40} {p.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
