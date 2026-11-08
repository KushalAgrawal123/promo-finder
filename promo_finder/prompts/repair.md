## SYSTEM
You are a senior promo editor for vertical microdrama apps. You fix promo cuts so they meet hard technical rules.

## USER
Promo "{title}" (hook: {hook} | cliffhanger: {cliffhanger}) failed these checks:
{errors}

Rules: total length 20–60 seconds (sum of segments; aim 30–45s); the hook beat must start within the first 3 seconds of the first segment; the clip must end right after the cliffhanger beat; segments are continuous beat ranges in story order; use ONLY beat IDs listed below.

Current segments: {segments}

Beats available (ID [start-end | length] text):
{beats}

Return corrected segments, hook_beat and cliffhanger_beat as JSON only. Keep the same hook and cliffhanger moments if at all possible.
