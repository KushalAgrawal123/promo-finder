# Quality report

Run `demo-default` — generator `gemini/gemini-3.5-flash`, judge `groq/openai/gpt-oss-120b`

## Pipeline funnel
- Candidates proposed by the generator: **3** (2 single-episode + 1 montages)
- Passed all rules on the first try: **3** (100%)
- Fixed by one LLM repair round: **0**
- Rejected (still failing after repair): **0**
- Removed as near-duplicates (IoU > threshold): **1**
- **Final validated promos: 2**

## Rule checks on final promos (enforced in code)
- Duration within 20–60s: 2/2
- Hook beat inside first 3s: 2/2
- Start auto-tightened onto the hook: 0
- End auto-trimmed to the cliffhanger: 2
- Hook/cliffhanger beat ID invalid → fallback used: 0

## Duration distribution
- 20–28s:  0
- 28–36s: ██ 2
- 36–44s:  0
- 44–52s:  0
- 52–60s:  0

## Stranger-judge scores (1–10, mean over final promos)
- Judged: 2/2
- hook_3s 5.50 · cliffhanger 7.50 · standalone 5.50 · intensity 7.50
- Would keep watching: 2/2
- Promos with ≥1 'confusing without context' note: 2
- Generator self-score vs judge: mean absolute gap 2.62 points (generator over-rates itself)

## Reject reasons

## Lowest standalone scores (where context is missing)
- E01-P1 'The Engagement Night Betrayal' standalone 5: The nature of Dev and Mira's relationship (engaged?) is only hinted at.; Tara's role and why she is with Dev are unclear.; Dev's line about marrying the bakery is cryptic without context.
- E02-P1 'The Forgery and The Dead Mother' standalone 6: The exact relationship between Mira, Dev, and Tara is never explained.; Why does the lawyer claim the real owner has been waiting twenty years?; Mira's mother is said to be dead, yet the lawyer presents a photo of her.