"""Write script.json for the AI News video from the locked research briefs."""
import json
from pathlib import Path

RUN = Path("/Users/zachrizzo/.video-studio/runs/run_1eb772e9")

# (segment_id, section_title, est_seconds, narration)
SEGS = [
 ("s00_intro", "Intro", 26,
  "This is your A.I. news rundown for the week of July 18th, 2026 — fourteen stories, fact-checked, straight through. "
  "A flagship model that still hasn't shipped. OpenAI's first piece of hardware. A 27 billion parameter model squeezed onto a phone. "
  "Meta pulling a tool after backlash. And sixteen Nobel laureates sounding the alarm. No hype — just what's actually confirmed, "
  "and what's only reported. Let's get into it."),
 ("s01_gemini", "Gemini 3.5 Pro delayed", 32,
  "First up: Google's Gemini 3.5 Pro. Despite all the buzz, it has not launched. Google blew past its original June target, and "
  "according to reporting, missed another deadline this month — with zero official specs, no model card, no pricing. Bloomberg says "
  "the holdup is disappointing coding performance and checkpoints that actually regressed. Reporting adds the rebuilt model still "
  "hallucinates and trails OpenAI's GPT-5.6 — but that part is from leaks, not Google. The confirmed core? It hasn't shipped, and "
  "Google is now the only major lab without a 2026 flagship out the door."),
 ("s02_codex", "OpenAI's $230 hardware", 31,
  "Next: OpenAI just shipped its first hardware — and it's a 230 dollar keypad. The Codex Micro, a limited-run programmable macropad "
  "built with Work Louder, is designed to drive OpenAI's Codex coding agents. It's a novelty, not their marquee device — that "
  "screenless A.I. companion is still years out. But the timing is the story: it dropped just five days after Apple sued OpenAI, "
  "alleging ex-Apple staff took trade secrets to build OpenAI's hardware. So OpenAI's very first gadget is landing in the middle of "
  "a lawsuit over how it builds gadgets."),
 ("s03_bonsai", "27B model in 4GB", 29,
  "Story three: a 27 billion parameter model that fits in about four gigabytes. PrismML's Bonsai 27B is a natively low-bit build of "
  "Qwen — its one-bit version is just 3.9 gigabytes. And it's not theoretical: it runs up to 163 tokens per second on an RTX "
  "fifty-ninety, and up to 87 on an Apple M5 Max. That's a frontier-class model running on consumer hardware, released under "
  "Apache 2.0. The compression tax that made big models desktop-only is shrinking fast."),
 ("s04_chips", "Everyone wants AI chips", 30,
  "Story four: everybody suddenly wants their own A.I. chips — but watch what's actually confirmed. Google's is real and shipping: "
  "its eighth-generation TPUs, designed in-house. Apple is different — it's only reportedly hunting A.I.-chip acquisitions to cut its "
  "Nvidia dependence, per The Information. No target, no deal, no Apple statement. And in the background, tool giant ASML has "
  "signaled price hikes — officially just 'pricing power,' with a reported ten percent figure ASML hasn't confirmed. Real trend, "
  "real tension — just mind the line between confirmed and reported."),
 ("s05_hyundai", "Hyundai strike", 30,
  "Story five: Hyundai workers walked out — but not purely over robots. On July 13th, unionized workers in Ulsan began a three-day "
  "partial strike, as Bloomberg reported. The primary fight is bonuses and stalled wage talks. Job-security guarantees against "
  "Hyundai's A.I.-driven humanoid robots — Boston Dynamics' Atlas — are a real demand, but a secondary one. It's an important nuance: "
  "this isn't the first 'A.I. strike,' it's a bonus dispute with an A.I. clause. And as talks deadlocked, it escalated toward "
  "longer daily stoppages."),
 ("s06_tiktok", "TikTok bans AI from livestreams", 31,
  "Story six: TikTok Shop is banning A.I. — from livestreams. New quality rules bar A.I.-generated voices, pre-recorded audio, and "
  "static or slideshow visuals from shopping livestreams, and now require real-time human narration. But note what it's not: it's "
  "not a blanket ban on A.I. influencers — avatars just can't fill more than half the screen, and A.I. is still fine for scripting "
  "and editing. Why do it? This lands against a projected 23.4 billion dollar U.S. TikTok Shop — and survey data showing shoppers "
  "are about four times more likely to trust a brand less when they spot A.I."),
 ("s07_race", "The AI race splits", 30,
  "Story seven: the global A.I. order is visibly splitting in two. On July 15th, China's regulator finally approved Apple "
  "Intelligence — after a roughly 22-month wait — but only with Alibaba's Qwen powering the on-device A.I. and Baidu handling "
  "visual search. American A.I., running on Chinese models, to clear China. And that very same week, Xi Jinping launched a "
  "29-nation World A.I. Cooperation Organisation in Shanghai — a rival bloc to the U.S.-led order. Two stories, one message: the "
  "A.I. world is picking sides."),
 ("s08_voice", "ChatGPT talks like a human", 29,
  "Story eight: ChatGPT's voice now talks like a person. On July 8th, OpenAI launched GPT-Live — a full-duplex voice model that "
  "listens and speaks at the same time, so it can be interrupted and answer naturally, replacing the old Advanced Voice Mode. The "
  "mini version is now the free default; harder questions get handed to GPT-5.5 in the background. In testing, users preferred it "
  "over the old voice mode about 76 percent of the time. It's about ten days old now — so, real, just not brand new."),
 ("s09_muse", "Meta's face tool, pulled", 30,
  "Story nine: Meta built a tool to put your face in A.I. images — then pulled it. Its Muse Image model let people generate images "
  "using the likeness of anyone with a public Instagram account — opt-out, not opt-in. Just tag them, no notification. Private "
  "accounts and minors were excluded. But Public Citizen, SAG-AFTRA, and the talent agency CAA pushed back hard, and within days "
  "Meta disabled the feature, saying it 'missed the mark.' Axios and others confirmed it. So this one's already over — but it's a "
  "preview of the consent fights ahead."),
 ("s10_meta", "Meta cut 8,000 jobs", 29,
  "Story ten: Meta cut eight thousand jobs for A.I. that its own C.E.O. admits isn't working yet. That's about ten percent of the "
  "workforce, gone in May — while Meta guides to between 125 and 145 billion dollars in 2026 A.I. spending. Then, at a July 2nd "
  "town hall, Zuckerberg conceded that over the prior four months, A.I. agent development 'hasn't really accelerated in the way that "
  "we expected.' Record spending, deep cuts, and an honest admission the payoff isn't here yet."),
 ("s11_ant", "Ant Group bets on robots", 27,
  "Story eleven: China's biggest fintech is pouring money into humanoid robots. Alibaba affiliate Ant Group led a 500 million yuan "
  "round — about 73.6 million dollars — in robotics startup Zeroth, which says it already has orders for more than thirty thousand "
  "units. And this isn't a one-off: it's Ant's twelfth robotics deal since the start of last year. Pair it with that Hyundai strike, "
  "and the theme is clear — humanoid robots are scaling fast, and the money knows it."),
 ("s12_antidoom", "Fixing AI 'doom loops'", 29,
  "Story twelve: a weird A.I. failure mode, and an open-source fix. Reasoning models sometimes fall into 'doom loops' — repeating "
  "themselves until the context window runs out. Liquid A.I. just open-sourced Antidoom, a training technique aimed at exactly that. "
  "On a third-party four-billion model, it cut the doom-loop rate from 22.9 percent to one percent. On one of Liquid's own "
  "checkpoints, from 10.2 down to 1.4. Code's on GitHub, Apache-licensed. A small, concrete look at how these models actually break "
  "— and get patched."),
 ("s13_hemispheric", "AI that reads your brain", 28,
  "Story thirteen: a startup that raised 52 million dollars to read your brain. Israeli company Hemispheric came out of six years of "
  "stealth to launch Descartes — a six-billion-parameter foundation model that decodes non-invasive brain activity, E.E.G., into "
  "quantitative diagnostics for psychiatric and neurological conditions. The hook: it was co-founded by a co-inventor of Apple's "
  "Face I.D. The pitch is reading the brain 'like a blood test,' from a fifteen-minute headset scan. A foundation model, pointed at "
  "the mind."),
 ("s14_nobel", "16 Nobel laureates warn", 28,
  "And story fourteen, the big one: two hundred economists and sixteen Nobel laureates just warned the world about A.I. Organized "
  "through Stanford's Digital Economy Lab and released July 13th, the statement — 'We Must Act Now' — argues A.I. could drive a "
  "transformation 'larger than the Industrial Revolution,' but compressed into a far shorter timeframe, with real risk of mass job "
  "displacement and widening inequality. When that many economists — and that many Nobel laureates — sign the same letter, it's "
  "worth reading."),
 ("s15_outro", "Outro", 20,
  "That's fourteen stories — every claim cross-checked, and every 'reportedly' kept where it belongs. The pattern this week? Huge "
  "spending, honest admissions the payoff isn't here yet, robots and chips scaling fast, and a lot of very smart people asking "
  "everyone to slow down and think. That's the rundown. See you next week."),
]

script = {
    "title": "AI News Rundown — July 18, 2026",
    "total_estimated_duration_seconds": float(sum(s[2] for s in SEGS)),
    "subject": "weekly AI news rundown, 14 fact-checked stories",
    "audience": "people who follow AI and want the accurate version",
    "narration_style": "Crisp, confident news anchor. Fast but clear. Skeptical — separates confirmed from reported.",
    "style_pack": "anthropic_docu",
    "segments": [
        {
            "segment_id": sid, "section_title": title, "narration_text": narr,
            "estimated_duration_seconds": float(dur), "animation_cues": [],
            "visual_engine": "collage", "visual_type": "diagram", "transition_type": "blur",
        }
        for (sid, title, dur, narr) in SEGS
    ],
}
(RUN / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
wc = sum(len(narr.split()) for _,_,_,narr in SEGS)
print(f"wrote script.json: {len(SEGS)} segments, ~{wc} words, est {sum(s[2] for s in SEGS)}s (~{sum(s[2] for s in SEGS)/60:.1f} min)")
