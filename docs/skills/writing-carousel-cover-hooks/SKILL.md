---
name: writing-carousel-cover-hooks
description: Use when writing, rewriting or auditing the cover headline (slide 1) of an Instagram news or tech carousel, especially when hooks read like flat news headlines ("COMPANY LAUNCHES X"), lead with a bare statistic, or are accurate but give a stranger no reason to swipe.
---

# Writing carousel cover hooks

## Overview

The cover is the only slide most people see. In under two seconds a stranger
must get **what happened** and feel **why it matters to them**. A report line
("ANTHROPIC RUNS A WET LAB") passes the first and fails the second. The fix is
not a better single line. It is five drafted lines on different levers, judged
against four checks.

## Output contract

Always produce exactly this, in this order:

```
ANGLE: <one sentence: what people assume vs what actually happened>

| # | Lever | Hook | Highlight | Words/Chars | Clear | For me | Open | True |
|---|-------|------|-----------|-------------|-------|--------|------|------|
| 1 | ...   | ...  | ...       | 8 / 39      | pass  | pass   | FLAG: why | pass |
(five rows, five different levers)

SHIP: <hook> | highlight: <phrase>
WHY: <one line naming the check it wins on>
```

## The levers (use five different ones)

| Lever | Shape | Example |
|---|---|---|
| curiosity_gap | Subject and setup plain; hold back the one detail slide 2 delivers | 53 CHATGPT PHOTOS LEAKED. NOBODY HACKED IN. |
| two_beat_contrast | Expectation. Twist. | THE CHATBOT SAID NO. THE ROBOT DIDN'T. |
| reader_stake | What changes for you | YOUR APP DOESN'T NEED GPT FOR YES OR NO |
| number_with_meaning | One number + what it counts + good/bad | 53 CHATGPT PHOTOS LEAKED. NO HACKER NEEDED. |
| winner_loser | Who gets it, who doesn't | DEFENDERS GET GOOGLE'S NEW AI. YOU WAIT. |
| consequence | What happened next, the unexpected part | AN AI TEST BROKE IN. NOBODY NOTICED FOR WEEKS. |
| question | Carries the stake; slide 2 answers it | WOULD YOU LET AN AI PAY FOR YOU? |

Start from the story's angle (the research summary's "what's surprising"
line). It usually holds the contrast; the hook compresses it.

## The four checks

1. **Clear**: someone who never heard of the company gets what happened in one glance.
2. **For me**: there is a stake the reader feels, not just a fact.
3. **Open**: a gap between what people assume and what happened, so they swipe to close it.
4. **True**: every word is backed by the source. Never stretch a fact to make it punchier.

Ship the row that passes all four. When several pass, prefer curiosity_gap
and two_beat_contrast. Tone: bold but factual, confident verbs, no hype
words (insane, crazy, mind-blowing, game-changing). On a tie, the shorter.

If the account has hooks its reviewer approved before, read them first and
match their voice and shape. They outrank every example in this skill.

## Hard rules

- At most 9 words; aim for 40 characters so it stays huge on three lines.
- Uppercase. Commas, full stops and apostrophes only (a question mark only for
  the question lever). No colon, em dash, emoji, hashtag or quote marks.
- Highlight: a verbatim 2-4 word substring of the hook, the payoff or the
  twist, never a connecting phrase like "OF THE".
- A number needs its meaning. "REFUSED 2 OF 100" fails: 2 of 100 what, and is
  that good? Say the side with the stake: "DIDN'T REFUSE 98 UNSAFE ORDERS".
- One subject's result is not a category's. If one of three models refused
  nothing, "ROBOT AI REFUSED 0 OF 100" is false.
- Two short statements are not the banned "not X, but Y" line.
- If the person dictates an exact title, ship it verbatim and say so.

## Report-line test

A hook that is subject + verb + object, with no "you/your", no second beat, no
number and no contrast word (but, no, only, still, without, nobody), is a
press release. Rewrite it from the angle.

| Report line (weak) | Rewrite | Lever |
|---|---|---|
| GPT-6 ASTRA REFUSED 2 OF 100 | THE CHATBOT SAID NO. THE ROBOT DIDN'T. | two_beat_contrast |
| GOOGLE OPENS NEW AI TO CYBER TEAMS FIRST | DEFENDERS GET GOOGLE'S NEW AI. YOU WAIT. | winner_loser |
| OPENAI AGENTS PUT USER IMAGES ONLINE | 53 CHATGPT PHOTOS LEAKED. NO HACKER NEEDED. | number_with_meaning |
| OPENAI AGENT GOT AROUND MEDICARE BLOCKS | AN AI TEST BROKE IN. NOBODY NOTICED FOR WEEKS. | consequence |

## Common mistakes

| Mistake | Fix |
|---|---|
| Five rewordings of one idea | Five different levers, not synonyms |
| Leading with the stat | Lead with the stake; give the number its meaning |
| Punchier than the facts | Run the True check against the source, word by word |
| Mystery hook that hides the subject ("YOU WON'T BELIEVE THIS") | Name the subject and the setup; a curiosity gap holds back one detail, never the subject |
| Newsroom verbs (BREACHED, UNVEILS, SLASHES) | Say it like a friend: broke into, launched, cut |
| Hook the slides can't back up | Readers punish bait; keep the promise on slide 2 |
