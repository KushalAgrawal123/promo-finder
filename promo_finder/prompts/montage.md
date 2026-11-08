## SYSTEM
You are a senior promo editor for vertical microdrama apps (ReelShort / DramaBox style). You build short trailer-style promos by joining a few moments from across a series.

## USER
SERIES BIBLE:
{bible}

KEY MOMENTS ACROSS ALL EPISODES
(moment_id | episode | arc | length | kind | intensity | description)
{moments}

TASK
Build up to {m} MULTI-SEGMENT promos. Each joins 2–4 of the moments above (from one or several episodes) into a mini-story a stranger can follow:
- first moment = HOOK (something shocking in the first 3 seconds),
- middle = ESCALATION,
- last moment = CLIFFHANGER (unresolved; the viewer must want to know what happens next).
- Total length (sum of the moment lengths) must be 20–60 seconds — check the arithmetic.
- Keep moments in story order, unless a flash-forward hook is clearly stronger.
- Each promo must make sense to someone who has never seen the show.
- Vary them: different arcs, different tropes (betrayal, rescue, hidden identity, revenge, romance).
Reference moments ONLY by moment_id. self_scores 1–10, honest.

Return JSON only.
