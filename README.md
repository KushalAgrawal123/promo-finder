# 🎬 Promo Finder

Reads a vertical-microdrama screenplay (one `.txt` per episode, with timestamps) and returns **ranked 20–60 second promo clips**. Each promo gives the exact episode and start–end timestamps of every segment, plus the hook, the cliffhanger and one line on why it works. A promo editor reviews the shortlist, approves or rejects, and exports.

Built for the Kuku AI Generalist assignment (Problem 1, *Find the Promos*).

## How it works

```
screenplays ─► [1] PARSE (code) ─► beats with IDs + timestamps (E03-B045, 01:13.10–01:14.30)
            ─► [2] SERIES BIBLE (LLM ×1): characters + speaker-label aliases, story arcs
            ─► [3] PER-EPISODE CANDIDATES (LLM ×N): promos as BEAT-ID ranges + hook/cliffhanger beats
            ─► [4] CROSS-EPISODE MONTAGES (LLM ×1): 2–4 key moments joined into a mini-trailer
            ─► [5] VALIDATE + SNAP (code): IDs → timestamps, start on the hook (≤3s), cut right after
                   the cliffhanger, never mid-line, 20–60s, dedupe   └─ fail → [5b] LLM repair ×1 → else reject
            ─► [6] STRANGER JUDGE (different LLM; sees ONLY the clip's text) scores hook/cliffhanger/standalone
            ─► [7] RANK (code): weighted score − penalties + early-episode bonus; diverse "post first" top 10
            ─► [8] REPORTS: promos.md / promos.csv / promos.json, quality_report.md, run_summary.json, raw LLM I/O
            ─► [9] EDITOR (human) in the Streamlit app: approve / reject / note → export
```

**The LLM never writes timestamps.** It picks beat IDs, and code converts them, so every timestamp exists in the screenplay and every cut lands on a line boundary.

| Code (deterministic, free) | AI (judgment) | Human (editor) |
|---|---|---|
| parsing, beat→time, cut-point snapping, 20–60s and hook-in-3s rules, dedupe, scoring, diversity ranking, reports, caching, cost tracking | understanding the story and aliases, proposing moments, writing hook/cliffhanger/why, judging clarity as a stranger, composing montages | approves the shortlist, fine-trims ±1s in the edit tool, posts. Feedback tunes prompts and weights |

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add free keys: GEMINI_API_KEY (aistudio.google.com), GROQ_API_KEY (console.groq.com)

# parse only (no API calls): sanity-check a new show's format
python -m promo_finder parse --input path/to/show_folder

# full run
python -m promo_finder run --input path/to/show_folder --out ../outputs
python -m promo_finder run --input examples/synthetic_show --out ../outputs --montages 1   # tiny demo show

# UI (run mode + viewer mode for a saved promos.json)
streamlit run app.py

# tests
pytest -q
```

Useful flags: `--episodes 1-10`, `--k 3` (max promos per episode), `--montages 8`, `--no-cache`, `--run-id NAME`.

### Swap the model (one line)
Set `models:` in `config.yaml`, or pass flags. Models are written `provider/model`:

```bash
--model gemini/gemini-3.5-flash          # default generator (free tier)
--judge-model groq/openai/gpt-oss-120b   # default judge (free tier, different model family)
--model anthropic/claude-opus-5-5        # native Anthropic SDK (ANTHROPIC_API_KEY)
--model openai/<model> | openrouter/<model> | ollama/qwen2.5:14b
--model custom/<model>                   # ANY OpenAI-compatible endpoint: set CUSTOM_LLM_BASE_URL + CUSTOM_LLM_API_KEY
```
You can also set the env vars `PROMO_GEN_MODEL` / `PROMO_JUDGE_MODEL`. Prompts live in `promo_finder/prompts/*.md` and can be edited without touching code.

### Built for free tiers
- **Disk cache** (`.cache/`): re-runs cost nothing, and an interrupted run resumes where it stopped.
- **Per-provider rate limiter**, backoff on 429/5xx, and a fallback model (logged) if the primary keeps failing.
- `run_summary.json` records calls, tokens and the list-price cost of the same run on paid models.

## Outputs (`outputs/<show>/<run_id>/`)
| file | what |
|---|---|
| `promos.md` | readable ranked report: "post these first" table, a card per promo, clip transcript |
| `promos.csv` | one row per promo for editors / spreadsheets |
| `promos.json` | everything (load it in the app's viewer mode) |
| `quality_report.md` | pipeline funnel, rule pass rates, duration histogram, judge scores, reject reasons |
| `run_summary.json` | models, tokens, time, list-price cost, failures, parse warnings |
| `rejected.json`, `duplicates.json` | what was dropped and why |
| `raw/` | every LLM request and response, unedited |

## Evaluation
```bash
python eval/evaluate.py --run ../outputs/<show>/<run_id>                   # gold recall@K + rule checks
python eval/evaluate.py --run RUN_A --compare RUN_B --episodes 1-15         # model / stability comparison
python eval/evaluate.py --run RUN --rating-template                         # CSV for blind human rating
```
`eval/gold.yaml` holds the must-find promo moments (by timestamp only).

## Input format expected
One file per episode containing `EPISODE NUMBER:`, scenes (`SCENE NN`, `Scene start/end`), and blocks like:
```
**MIRA (Shattered):**
Start: (00:19.10) | End: (00:21.50)
"You were going to marry me tomorrow."
```
`CAMERA`, `ACTION` and `SOUND FX` blocks are recognised. Every other label is a speaker. Headers are case-insensitive. Malformed blocks become warnings, not crashes.

## Confidentiality
The input pack (the show's screenplays) and any output quoting it are **not** in this repo (`.gitignore`). `examples/synthetic_show/` is a short fake show written for tests and demos.

## Limits (honest)
- It works from the screenplay, not the video. It can't see acting, pacing or how strong a visual really is, and final-cut timestamps may drift by about 1s from the script.
- Scores come from an LLM judging text. They're useful for ranking, but not ground truth; real performance data (3s hold, CTR, installs) should recalibrate the weights.
- The quality of montages (cross-episode joins) depends on the key moments found per episode, and cuts between them may feel abrupt.
