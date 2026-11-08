"""Promo Finder — Streamlit UI for promo editors.

Run mode:    upload screenplay .txt files (or a .zip) -> run the pipeline -> ranked promos.
Viewer mode: upload a promos.json from an earlier run -> browse without any API calls.
Either way:  approve / reject each promo and export the approved list (the human-in-the-loop step).
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from promo_finder.parse import parse_texts  # noqa: E402
from promo_finder.pipeline import load_config, run  # noqa: E402

MAX_EPISODES = int(os.environ.get("PROMO_MAX_EPISODES", "60"))

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")

# Streamlit Cloud secrets -> env (never committed; set in the app's Secrets panel)
SECRETS_ERROR = ""
try:
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
              "CUSTOM_LLM_BASE_URL", "CUSTOM_LLM_API_KEY"):
        if k in st.secrets and not os.environ.get(k):
            os.environ[k] = str(st.secrets[k]).strip()
except Exception as e:  # noqa: BLE001
    # "No secrets found" is normal when running locally with .env; anything else (e.g. a TOML parse
    # error in the Streamlit Cloud Secrets box) must be shown, not swallowed.
    if "No secrets found" not in str(e):
        SECRETS_ERROR = str(e)

cfg = load_config()
st.title("🎬 Promo Finder")
st.caption("Reads a microdrama screenplay and finds ranked 20–60s promo clips: exact episode + timestamps, "
           "hook, cliffhanger and why it works. The AI picks beats; code computes every timestamp.")

mode = st.sidebar.radio("Mode", ["Run on screenplays", "View a saved run (promos.json)"])

# ---------------- sidebar: models & weights ----------------
with st.sidebar.expander("Models", expanded=mode == "Run on screenplays"):
    presets = [cfg["models"]["generator"], "groq/openai/gpt-oss-120b", "groq/qwen/qwen3.8-27b",
               "anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "ollama/qwen2.5:14b", "custom…"]
    gen = st.selectbox("Generator", presets, index=0)
    if gen == "custom…":
        gen = st.text_input("Generator provider/model", "custom/my-model")
    jpresets = [cfg["models"]["judge"], cfg["models"]["generator"], "anthropic/claude-haiku-5-5", "custom…"]
    jud = st.selectbox("Judge (sees only the clip)", jpresets, index=0)
    if jud == "custom…":
        jud = st.text_input("Judge provider/model", "custom/my-model")
    st.caption("Optional keys (kept in this session only):")
    keys = {}
    for env in ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        v = st.text_input(env, type="password", placeholder="set" if os.environ.get(env) else "")
        if v:
            keys[env] = v.strip()


def missing_keys(*model_refs: str) -> list[str]:
    """API-key env vars needed by the chosen models that are not available."""
    out = []
    for ref in model_refs:
        env = (cfg.get("providers", {}).get(ref.split("/", 1)[0], {}) or {}).get("api_key_env")
        if env and not (keys.get(env) or os.environ.get(env)) and env not in out:
            out.append(env)
    return out


if SECRETS_ERROR:
    st.sidebar.error(f"Could not read the app's Secrets (check the TOML format): {SECRETS_ERROR}")

with st.sidebar.expander("Ranking weights"):
    w = cfg["ranking"]["weights"]
    for dim in list(w):
        w[dim] = st.slider(dim, 0.0, 1.0, float(w[dim]), 0.05)
    cfg["ranking"]["funnel_bonus"] = st.slider("Early-episode bonus (ep ≤ 10)", 0.0, 2.0,
                                               float(cfg["ranking"]["funnel_bonus"]), 0.1)


def render_results(promos: list[dict], meta: dict, key_prefix: str):
    if "decisions" not in st.session_state:
        st.session_state.decisions = {}
    dec = st.session_state.decisions
    st.subheader(f"{meta.get('show', '')} — {len(promos)} promos")
    st.caption(f"generator `{meta.get('generator')}` · judge `{meta.get('judge')}` · run `{meta.get('run_id')}`")

    def segs(p):
        return "; ".join(f"E{s['episode']:02d} {s['start_tc']}–{s['end_tc']}" for s in p["segments"])

    pf = [p for p in promos if p.get("post_first")]
    if pf:
        st.markdown("### ⭐ Post these first")
        st.dataframe(pd.DataFrame([{"rank": p["rank"], "promo": p["title"], "segments": segs(p),
                                    "len (s)": round(p["total_duration"]), "score": p["score"],
                                    "why post first": p["why_post_first"]} for p in pf]),
                     hide_index=True, width="stretch")

    c1, c2, c3 = st.columns(3)
    only_pf = c1.checkbox("Only 'post first'", False, key=f"{key_prefix}_pf")
    src = c2.selectbox("Source", ["all", "episode", "montage"], key=f"{key_prefix}_src")
    if len(promos) > 5:
        n_show = c3.slider("Show top", 5, len(promos), min(25, len(promos)), key=f"{key_prefix}_n")
    else:  # a slider needs min < max; small runs just show everything
        n_show = len(promos)
    shown = [p for p in promos if (not only_pf or p.get("post_first")) and (src == "all" or p["source"] == src)][:n_show]

    for p in shown:
        j = p.get("judge") or {}
        d = dec.get(p["id"], {}).get("decision")
        badge = {"approve": "✅ ", "reject": "❌ "}.get(d, "")
        with st.expander(f"{badge}#{p['rank']} · {p['title']} · score {p['score']} · {round(p['total_duration'])}s"
                         f"{' · ⭐' if p.get('post_first') else ''}", expanded=False):
            st.dataframe(pd.DataFrame([{"segment": i + 1, "episode": s["episode"], "start": s["start_tc"],
                                        "end": s["end_tc"], "seconds": s["duration"]}
                                       for i, s in enumerate(p["segments"])]), hide_index=True)
            st.markdown(f"**Hook (0–3s):** {p['hook']}  \n**Cliffhanger:** {p['cliffhanger']}  \n"
                        f"**Why it works:** {p['why_it_works']}  \n**Arc / tropes:** {p.get('arc', '')} / "
                        f"{', '.join(p.get('tropes', []))}")
            if j:
                st.markdown(f"**Stranger judge:** hook {j.get('hook_3s')} · cliffhanger {j.get('cliffhanger')} · "
                            f"standalone {j.get('standalone')} · intensity {j.get('intensity')} — _{j.get('verdict')}_")
                if j.get("confusions"):
                    st.warning("Confusing without context: " + "; ".join(j["confusions"]))
            with st.popover("Clip transcript"):
                st.code(p.get("transcript", ""), language=None)
            a, b, c = st.columns([1, 1, 3])
            if a.button("Approve", key=f"{key_prefix}_a_{p['id']}"):
                dec[p["id"]] = {**dec.get(p["id"], {}), "decision": "approve"}
                st.rerun()
            if b.button("Reject", key=f"{key_prefix}_r_{p['id']}"):
                dec[p["id"]] = {**dec.get(p["id"], {}), "decision": "reject"}
                st.rerun()
            note = c.text_input("Editor note", dec.get(p["id"], {}).get("note", ""), key=f"{key_prefix}_n_{p['id']}")
            if note:
                dec[p["id"]] = {**dec.get(p["id"], {}), "note": note}

    approved = [p for p in promos if dec.get(p["id"], {}).get("decision") == "approve"]
    st.markdown(f"### Export — {len(approved)} approved, "
                f"{sum(1 for v in dec.values() if v.get('decision') == 'reject')} rejected")
    rows = [{"rank": p["rank"], "id": p["id"], "title": p["title"], "segments": segs(p),
             "duration_s": p["total_duration"], "hook": p["hook"], "cliffhanger": p["cliffhanger"],
             "note": dec.get(p["id"], {}).get("note", "")} for p in approved]
    e1, e2 = st.columns(2)
    e1.download_button("Download approved (CSV)", pd.DataFrame(rows).to_csv(index=False), "approved_promos.csv",
                       disabled=not rows)
    fb = [{"id": k, **v, "rank": next((p["rank"] for p in promos if p["id"] == k), None)} for k, v in dec.items()]
    e2.download_button("Download editor feedback (JSON)", json.dumps(fb, indent=2), "editor_feedback.json",
                       disabled=not fb)
    st.caption("Feedback (approve/reject + notes) is what you'd use to tune prompts and ranking weights over time.")


# ---------------- viewer mode ----------------
if mode == "View a saved run (promos.json)":
    up = st.file_uploader("promos.json from a previous run", type=["json"])
    local = st.text_input("…or a path to promos.json on this machine (local use)", "")
    demo = st.checkbox("Load the demo run instead (synthetic 2-episode show, no API calls)", value=not up and not local)
    data = None
    if up:
        data = json.load(up)
    elif local and Path(local).expanduser().is_file():
        data = json.loads(Path(local).expanduser().read_text())
    elif demo:
        data = json.loads((ROOT / "examples" / "demo_run" / "promos.json").read_text())
    if data:
        render_results(data["promos"], {**data.get("meta", {}), "show": data.get("show")}, "view")
    else:
        st.info("Upload a `promos.json` (e.g. from the shared raw-outputs folder) to browse a run without API calls.")
    st.stop()

# ---------------- run mode ----------------
files = st.file_uploader("Screenplay files — one .txt per episode, or a .zip", type=["txt", "zip"],
                         accept_multiple_files=True)
use_demo = st.checkbox("No files? Use the built-in demo show (2 synthetic episodes in the same format)",
                       value=False, disabled=bool(files))
title = st.text_input("Show title", "Synthetic Show (demo)" if use_demo and not files else "Uploaded show")
named: list[tuple[str, str]] = []
if use_demo and not files:
    for p in sorted((ROOT / "examples" / "synthetic_show").glob("*.txt")):
        named.append((p.name, p.read_text(encoding="utf-8")))
for f in files or []:
    if f.name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(f.read())) as z:
            for n in z.namelist():
                if n.lower().endswith(".txt") and not Path(n).name.startswith((".", "__")):
                    named.append((Path(n).name, z.read(n).decode("utf-8", errors="replace")))
    else:
        named.append((f.name, f.read().decode("utf-8", errors="replace")))

if not named:
    st.info("Upload screenplays to start, or tick the demo-show box above. To browse finished results without "
            "running anything, switch to **View a saved run** in the sidebar.")
    st.stop()

show = parse_texts(named, title=title)
n_eps = len(show.episodes)
st.success(f"Parsed {n_eps} episodes · {sum(len(e.beats) for e in show.episodes)} timestamped beats"
           + (f" · ⚠️ {len(show.warnings)} warnings" if show.warnings else ""))
if show.warnings:
    with st.expander("Parse warnings"):
        st.write(show.warnings[:50])
nums = [e.number for e in show.episodes]
c1, c2, c3 = st.columns(3)
lo, hi = c1.select_slider("Episodes", options=nums, value=(nums[0], nums[-1])) if len(nums) > 1 else (nums[0], nums[0])
k = c2.number_input("Max promos per episode", 1, 5, cfg["pipeline"]["candidates_per_episode"])
montages = c3.number_input("Cross-episode montages", 0, 12,
                           1 if (use_demo and not files) else cfg["pipeline"]["montages"])
if sum(1 for n in nums if lo <= n <= hi) > MAX_EPISODES:
    st.error(f"This deployment processes at most {MAX_EPISODES} episodes per run.")
    st.stop()

need = missing_keys(gen)          # the generator key is required
soft = [k for k in missing_keys(jud) if k not in need]   # judge falls back to the generator model
if need:
    st.warning("Missing API key: " + ", ".join(f"`{n}`" for n in need) + ". Paste it in the sidebar under "
               "**Models**, or (app owner) add it in Streamlit Cloud → Manage app → Settings → Secrets as "
               f"`{need[0]} = \"...\"`. To browse results without keys, use **View a saved run**.")
elif soft:
    st.caption(f"No `{soft[0]}` set: the judge will fall back to `{cfg['models'].get('judge_fallback')}`.")
if st.button("Find promos", type="primary", disabled=bool(need)):
    bar = st.progress(0.0, "Starting…")
    logbox = st.empty()
    logs: list[str] = []
    weights = {"bible": (0.0, 0.08), "candidates": (0.08, 0.6), "montage": (0.6, 0.68), "validate": (0.68, 0.78),
               "judge": (0.78, 0.98), "done": (1.0, 1.0)}

    def progress(stage, done, total, msg=""):
        a, b = weights.get(stage, (0, 1))
        bar.progress(min(1.0, a + (b - a) * (done / max(total, 1))), f"{stage}: {msg}")

    def log(msg):
        logs.append(msg)
        logbox.code("\n".join(logs[-12:]), language=None)

    out_root = Path(tempfile.gettempdir()) / "promo_finder_runs"
    try:
        res = run(show, out_root, cfg=cfg, generator=gen, judge_model=jud, episodes=(lo, hi), k=int(k),
                  montages=int(montages), api_keys=keys, progress=progress, log=log)
    except Exception as e:  # noqa: BLE001
        st.error(f"Run failed: {e}")
        st.stop()
    data = json.loads((Path(res["out_dir"]) / "promos.json").read_text())
    st.session_state.last_run = data
    st.session_state.last_out = res["out_dir"]
    st.session_state.decisions = {}
    s = res["summary"]
    st.info(f"{s['final_promos']} promos in {s['wall_seconds']}s · {s['llm_calls']} LLM calls "
            f"({s['cached_calls']} cached) · {s['input_tokens']:,} in / {s['output_tokens']:,} out tokens")

if "last_run" in st.session_state:
    data = st.session_state.last_run
    out_dir = Path(st.session_state.last_out)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in out_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(out_dir))
    st.download_button("Download all outputs (zip: promos.json/csv/md, quality report, raw LLM I/O)", buf.getvalue(),
                       f"promo_finder_{data['meta']['run_id']}.zip")
    render_results(data["promos"], {**data["meta"], "show": data["show"]}, "run")
