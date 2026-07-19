# Script voice — the house contract for narration

Applies to EVERY video this studio produces. Learned from a real episode whose
script was factually perfect and still sounded synthetic. The core finding:
**most AI tells aren't bad sentences — they're good patterns repeated at
perfect frequency.** One kicker is fine; a kicker per story is a signature.

The mechanically detectable subset is linted by QA (`voice.*` warnings in
`src/qa/run_qa.py:detect_ai_tells`). The rest is a writing contract.

## What narration IS

Spoken copy a host would say to a friend, on mic, in one take: contractions,
short sentences, a real point of view, occasional questions to the viewer,
varied rhythm. Facts stay verified; *reactions* carry the personality
("honestly, this one's disappointing", "I did not expect this").

## The tells (each observed in a real script — don't repeat them)

| Tell | Example from the wild | Instead |
|---|---|---|
| Kicker on every story | "A foundation model, pointed at the mind." ×15 | Max 2–3 kickers per episode; most stories just end or roll into the next |
| "X, one message: Y" summarizer | "Two stories, one message: the AI world is picking sides." | Cut the thesis sentence; let the pairing speak |
| Colon-reveal openers | "First up: …" / "Story three: …" | Start inside the story; numbering lives on-screen, never spoken |
| Labeling your own rhetoric | "The hook: it was co-founded by…" | Deliver the hook; never name it |
| Explicit-nuance lecture | "But note what it's not: …" / "It's an important nuance:" | Just say the true thing plainly |
| Editorial-process language | "fact-checked", "every claim cross-checked", "confirmed vs reported" | Describe the story, never the research behind it; hedges stay attributed ("Bloomberg says", "reportedly") |
| Precision-with-hedge speak | "roughly 22-month wait", "about 76 percent" | Round aloud ("almost two years", "three out of four"); the screen carries exact figures |
| Appositive chains | "GPT-Live — a full-duplex model that…, so it can…, replacing…" | One job per sentence; split or cut |
| Uniform everything | 15 segments × ~70 words × same shape | Vary: quick 15s hits, one 60s deep dive, a rapid-fire round |
| "It's not X — it's Y" contrast frame | "It's a novelty, not their marquee device." | Fine once per episode; never a house style |
| Zero "I", zero questions | (the entire episode) | Host has takes; ask the viewer something at least twice |
| Essay-conclusion outro | "The pattern this week? Huge spending, honest admissions…" | Sign off like a person: one genuine reaction + a question + "see you Friday" |

## Episode-level moves that read human

- **Cold open** with the single wildest fact before any "welcome to the show".
- **Callbacks** between stories ("remember that strike from two minutes ago? Same theme.").
- **Tease the ender** mid-episode ("stick around — the Nobel letter is the big one").
- **Table read**: synthesize ONE segment, listen to it, rewrite anything you
  wouldn't say to a friend — then apply that ear everywhere. Punctuation is
  pacing for TTS: em-dashes and ellipses are beats; sentences under ~20 words.
