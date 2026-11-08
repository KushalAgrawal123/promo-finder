## SYSTEM
You are a story editor at a vertical-microdrama studio. You read show material carefully and never invent facts.

## USER
Below are the loglines and continuity notes for every episode of a show, plus the speaker labels used in each episode.

Produce a compact series bible as JSON:
- premise: 2 sentences.
- characters: main and recurring characters. For each: name, aliases (EVERY speaker label that refers to this character — labels like "WOMAN IN RED DRESS", "TALL STRANGER", "THE EX-FIANCE" are often descriptive aliases of named characters; match them using the loglines and the episodes they appear in), role (one line: who they are to the heroine/hero).
- arcs: 4 to 8 story arcs that together cover every episode, each with first_episode, last_episode and a one-line summary.
- tropes: the genre tropes this show sells (e.g. public humiliation, hidden identity, revenge, forced marriage).
- uncertain_aliases: labels you could not confidently map.

Use only what the text says.

SHOW TITLE: {title}

EPISODE INDEX:
{episode_index}
