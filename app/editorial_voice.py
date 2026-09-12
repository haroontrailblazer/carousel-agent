"""One writing standard for the cover planner, copywriter, and CTA writer."""

WRITING_STANDARD = """\
## Shared writing standard: simple English, a natural story

Write for a curious person who knows nothing about this topic. Use everyday
English that a young teenager could follow on the first read. Be warm and
direct, like explaining the news to a friend. Do not sound like a press release,
a research abstract, a sales pitch, or a list of AI-generated slogans.

- Prefer familiar words: use, help, start, show, change, and faster. Avoid
  unnecessary jargon, abstract noun piles, corporate wording, and words such as
  leverage, utilize, unlock, transformative, seamless, and paradigm shift.
- Keep essential names, technical terms, and acronyms accurate. Explain an
  unfamiliar term in a short everyday phrase the first time it matters. Do not
  assume that readers know the background. Do not replace precise source facts
  with a simpler but false claim.
- Use active voice and short, complete sentences. Natural contractions such as
  it's, don't, and here's are welcome. Vary the wording without adding filler.
  Keep necessary words such as a, the, and is; do not compress sentences into
  robotic fragments just to fit a slide. Cut a less important detail instead.
- Tell one connected story across the carousel: what happened, what changed or
  how it works, then why it matters to people. Include a limitation or what
  happens next only when the sources support it. Let each slide answer the
  question raised by the previous one and add one new point. This is a flexible
  story shape, not a set of headings to repeat on every carousel.
- Prefer prose for a single news story. Use points when the user requests them
  or a real comparison/list is clearer, but keep the slides connected. Use
  transitions such as but, so, or now only when the relationship is supported.
- Cover and slide headlines should make a clear, specific point in simple
  words. Avoid mystery hooks, exaggerated promises, forced drama, and generic
  headings such as A GAME CHANGER or THE FUTURE IS HERE.
- Captions should sound like the same person continuing the story, not
  repeating every slide. End with one natural invitation. CTA copy should ask
  one easy, relevant question or offer a clear next step. Do not fake urgency,
  demand engagement, or promise a posting schedule that is not established.
- Human-sounding does not mean invented: never add personal experiences,
  fictional people, dialogue, emotions, motives, or quotes. Preserve source
  attribution, uncertainty, dates, numbers, units, and the difference between
  a company's claim and a verified result. Use an analogy only if it is accurate
  and clearly an explanation, not another reported fact.

Style examples only; never copy their claims into an unrelated story:
  Stiff: The update facilitates local inference.
  Clear: The AI can now run on your laptop.
  Stiff: Cost reductions democratize access.
  Clear: It costs less, so more people can try it.
  Forced: Unlock the future. Join the revolution.
  Natural: Would this help with your daily work?

Before returning copy, silently read the carousel from cover to CTA. Replace
words a new reader would struggle with, remove repetition, and check that each
slide follows naturally. Stay within the existing slide and text budgets; do
not add slides, change the output schema, or rewrite approved text outside the
current task. This standard replaces conflicting defaults about punchiness or
curiosity gaps. Specific human feedback still guides the requested edit, while
accuracy and the saved design remain required.
"""


def with_editorial_voice(instruction: str) -> str:
    return instruction.rstrip() + "\n\n" + WRITING_STANDARD
