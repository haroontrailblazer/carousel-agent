"""A typed request's time words become UTC dates, and research is held to them.

Asked on 26 September 2026 for "this week major ai updates like new model
launches", research brought back March launches and the carousel called them
this week's news (run-3c4107e390df): no prompt knew the date and nothing read
"this week" as dates. The fixtures below are the key facts those runs saved.
Pure functions, no network.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import time_window as tw

NOW = datetime(2026, 9, 26, 6, 24, tzinfo=timezone.utc)
TODAY = NOW.date()

RUN_3C41_TOPIC = "this week major ai updates like new model launches and the comparative open weight models"
# run-3c4107e390df: 'this week major ai updates like new model launches and the comparative open weight models'
RUN_3C41_FACTS = [
    {'fact': 'OpenAI announced \u201cGPT\u20115.4 mini and nano\u201d on March 17, 2026.', 'source_url': 'https://openai.com/index/introducing-gpt-5-4-mini-and-nano/'},
    {'fact': 'OpenAI describes GPT\u20115.4 mini and GPT\u20115.4 nano as fast, efficient models optimized for coding and subagents.', 'source_url': 'https://openai.com/index/introducing-gpt-5-4-mini-and-nano/'},
    {'fact': 'Mistral announced Mistral Small 4 on March 16, 2026.', 'source_url': 'https://mistral.ai/news/mistral-small-4/'},
    {'fact': 'Mistral Small 4 is released under the Apache 2.0 license.', 'source_url': 'https://docs.mistral.ai/models/mistral-small-4-0-26-03'},
    {'fact': 'Mistral Small 4 has 119B total parameters and 6.5B active parameters per token.', 'source_url': 'https://docs.mistral.ai/models/mistral-small-4-0-26-03'},
    {'fact': 'Mistral Small 4 has a 256k context window and accepts text and image inputs.', 'source_url': 'https://docs.mistral.ai/models/mistral-small-4-0-26-03'},
    {'fact': 'Mistral Small 4 unifies instruct, reasoning, and coding capabilities in one model.', 'source_url': 'https://mistral.ai/news/mistral-small-4/'},
    {'fact': 'Standard Mistral Small 4 inference costs $0.15 per million input tokens, $0.015 per million cached input tokens, and $0.60 per million output tokens.', 'source_url': 'https://docs.mistral.ai/inference/pricing'},
    {'fact': 'Mistral reports a 40% reduction in end-to-end completion time for Mistral Small 4 in a latency-optimized setup.', 'source_url': 'https://mistral.ai/news/mistral-small-4/'},
    {'fact': 'Mistral reports that Mistral Small 4 serves 3\xd7 more requests per second than Mistral Small 3 in a throughput-optimized setup.', 'source_url': 'https://mistral.ai/news/mistral-small-4/'},
    {'fact': 'On AA LCR, Mistral reports a score of 0.72 for Mistral Small 4 with 1.6K generated characters, versus 5.8\u20136.1K characters for comparable Qwen runs.', 'source_url': 'https://mistral.ai/news/mistral-small-4/'},
    {'fact': 'On LiveCodeBench, Mistral says Mistral Small 4 outperforms GPT\u2011OSS 120B while using 20% less output.', 'source_url': 'https://mistral.ai/news/mistral-small-4/'},
    {'fact': 'The official Mistral Small 4 repository is mistralai/Mistral-Small-4-119B-2603 on Hugging Face.', 'source_url': 'https://huggingface.co/mistralai/Mistral-Small-4-119B-2603'},
    {'fact': 'Mistral launched Voxtral TTS on March 23, 2026.', 'source_url': 'https://mistral.ai/news/voxtral-tts/'},
    {'fact': 'Voxtral TTS is a 4B-parameter text-to-speech model supporting 9 languages.', 'source_url': 'https://mistral.ai/news/voxtral-tts/'},
    {'fact': 'Mistral offers Voxtral TTS open weights on Hugging Face under CC BY-NC 4.0.', 'source_url': 'https://mistral.ai/news/voxtral-tts/'},
    {'fact': 'The Hugging Face OpenEvals leaderboard data lists DeepSeek\u2011V3.2 with an aggregate score of 64.28 across 9 benchmarks.', 'source_url': 'https://huggingface.co/datasets/OpenEvals/leaderboard-data/blob/main/leaderboard.json'},
    {'fact': 'The OpenEvals data lists DeepSeek\u2011V3.2 at 85.0 on MMLU\u2011Pro, 82.4 on GPQA Diamond, 94.17 on AIME 2026, and 70.0 on SWE\u2011bench Verified.', 'source_url': 'https://huggingface.co/datasets/OpenEvals/leaderboard-data/blob/main/leaderboard.json'},
    {'fact': 'The Hugging Face model page for DeepSeek\u2011V3.2 identifies its license as MIT.', 'source_url': 'https://huggingface.co/deepseek-ai/DeepSeek-V3.2'},
]
# run-79e4f1792845: 'AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this week September 2026'
RUN_79E4_FACTS = [
    {'fact': 'Google announced Gemini 3.8 Flash and Gemini 3.8 Flash Cyber on September 2, 2026.', 'source_url': 'https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/'},
    {'fact': 'The Gemini API release notes list Gemini 3.8 Flash as generally available on September 2, 2026.', 'source_url': 'https://ai.google.dev/gemini-api/docs/changelog'},
    {'fact': 'Google described Gemini 3.8 Flash as its strongest reasoning and coding model to date, delivered at the same speed and low cost as Gemini 3.7.', 'source_url': 'https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/'},
    {'fact': 'Gemini 3.8 Flash launched with introductory pricing of $0.75 per 1 million input tokens and $3.75 per 1 million output tokens.', 'source_url': 'https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/'},
    {'fact': 'Google said Gemini 3.8 Flash standard pricing would change on January 1, 2027, to $1.50 per 1 million input tokens and $7.50 per 1 million output tokens.', 'source_url': 'https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/'},
    {'fact': 'Xiaomi\u2019s official MiMo-V2.6 release page is dated September 22, 2026.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'Xiaomi said it fully open-sourced the MiMo-V2.6 weights and technical report.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'Xiaomi reported a score of 46.32 for MiMo-V2.6-Pro on Artificial Analysis\u2019 composite intelligence index.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'Xiaomi says MiMo-V2.6-Pro-UltraSpeed delivers up to 20 times the output speed of MiMo-V2.6-Pro.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro-ultraspeed'},
    {'fact': 'Xiaomi lists overseas MiMo-V2.6-Pro prices of $0.0036 per 1 million cache-hit input tokens, $0.435 per 1 million cache-miss input tokens, and $0.87 per 1 million output tokens.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro'},
    {'fact': 'Xiaomi lists overseas MiMo-V2.6-Pro-UltraSpeed prices of $0.036 per 1 million cache-hit input tokens, $4.35 per 1 million cache-miss input tokens, and $8.70 per 1 million output tokens.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro-ultraspeed'},
    {'fact': 'Meta\u2019s Muse Glimmer announcement was published August 10, 2026, not in September; Meta describes it as a 30-billion-parameter model released under Apache 2.0 for always-on local agent workflows, with open weights on Hugging Face.', 'source_url': 'https://research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model'},
]
# run-aa99162e2fe0: 'AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this 25th September 2026'
RUN_AA99_FACTS = [
    {'fact': 'Xiaomi officially released and open-sourced MiMo-V2.6 on September 22, 2026.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'The MiMo-V2.6 series includes two native fully multimodal models: MiMo-V2.6-Pro and MiMo-V2.6-Flash.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'Xiaomi\u2019s open-source package includes model weights, a technical report, MiMo-V2.6-Distill-Qwen-9B, reinforcement-learning resources, and training code.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'MiMo-V2.6 can work from images, videos, or text, and the Pro model lists a 1M-token context window.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro'},
    {'fact': 'Xiaomi advertises MiMo-V2.6-Pro UltraSpeed at up to 20\xd7 output speed.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro-ultraspeed'},
    {'fact': 'The standard MiMo-V2.6-Pro API is priced at $0.0036 per million cache-hit input tokens, $0.435 per million cache-miss input tokens, and $0.87 per million output tokens.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro'},
    {'fact': 'MiMo-V2.6-Pro UltraSpeed is priced at $0.036 per million cache-hit input tokens, $4.35 per million cache-miss input tokens, and $8.70 per million output tokens.', 'source_url': 'https://mimo.mi.com/models/en-US/mimo-v2.6-pro-ultraspeed'},
    {'fact': 'Xiaomi reports DeepSWE v1.1 improving from 58.4 to 72.6 for MiMo-V2.6-Pro and from 48.8 to 65.7 for MiMo-V2.6-Flash.', 'source_url': 'https://mimo.mi.com/docs/en-US/news/latest/v2-6'},
    {'fact': 'On September 24, 2026, Liquid AI published an experimental open-weight draft model for LFM2.5-VL-3B that adds speculative decoding.', 'source_url': 'https://huggingface.co/blog/LiquidAI/lfm2-5-vl-dspark'},
    {'fact': 'Liquid AI reports decode speedups of up to 3.13\xd7 on-device and 2.66\xd7 on an H100, with end-to-end gains up to 2.62\xd7 and 2.27\xd7, respectively.', 'source_url': 'https://huggingface.co/blog/LiquidAI/lfm2-5-vl-dspark'},
    {'fact': 'Liquid AI says its open-weight LFM2.5-2.6B decodes at 220 tokens per second on an M5 Max and 113 tokens per second on a Ryzen AI Max+ 395 while staying under 2.5 GB.', 'source_url': 'https://www.liquid.ai/blog/lfm2-5-2-6b'},
    {'fact': 'The supplied news item is titled \u201cAI MODELS RACE ON SPEED AND OPEN WEIGHTS on this 25th September 2026.\u201d', 'source_url': ''},
]


def typed(text: str, now: datetime = NOW) -> dict:
    """The window of a topic typed into the console (the orchestrator's NewsItem)."""
    return tw.resolve({"id": "t", "title": text.splitlines()[0][:150], "body": text,
                       "source_name": "adhoc"}, now)


# --- resolve --------------------------------------------------------------


@pytest.mark.parametrize("text, start, end", [
    ("this week major ai updates like new model launches and the comparative open weight models",
     "2026-09-20", "2026-09-26"),
    ("AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this 25th September 2026", "2026-09-19", "2026-09-25"),
    ("AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this week September 2026", "2026-09-20", "2026-09-26"),
    ("OpenAI news yesterday", "2026-09-24", "2026-09-26"),
    ("what happened today in AI", "2026-09-25", "2026-09-26"),
    ("today's AI news", "2026-09-25", "2026-09-26"),
    ("AI launches in the last 7 days", "2026-09-20", "2026-09-26"),
    ("AI launches last week", "2026-09-13", "2026-09-26"),
    ("AI news this month", "2026-09-01", "2026-09-26"),
    ("AI news last month", "2026-08-01", "2026-09-26"),
    ("AI model launches in August 2026", "2026-08-01", "2026-08-31"),
])
def test_period_words_and_named_dates_become_strict_windows(text, start, end):
    window = typed(text)
    assert (window["start"], window["end"], window["strict"], window["oldest"]) == (start, end, True, start)
    assert window["requested_on"] == "2026-09-26"
    assert window["phrase"]


def test_this_month_early_in_a_month_still_covers_a_week():
    window = typed("AI news this month", datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert (window["start"], window["end"]) == ("2026-09-27", "2026-10-03")


def test_a_named_month_is_capped_at_the_request_date():
    window = typed("what launched in September 2026")
    assert (window["start"], window["end"]) == ("2026-09-01", "2026-09-26")


def test_the_request_date_is_the_utc_date():
    late_evening_in_new_york = datetime(2026, 9, 26, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    window = typed("AI news this week", late_evening_in_new_york)
    assert window["requested_on"] == "2026-09-27"
    assert (window["start"], window["end"]) == ("2026-09-21", "2026-09-27")


def test_trending_news_is_a_soft_window_with_an_age_limit():
    window = typed("trending AI news")
    assert window == {"requested_on": "2026-09-26", "phrase": "trending", "start": "2026-09-13",
                      "end": "2026-09-26", "strict": False, "oldest": "2026-08-27"}


def test_latest_on_an_explainer_has_no_age_limit():
    window = typed("latest iPhone camera features")
    assert (window["strict"], window["start"], window["oldest"]) == (False, "2026-09-13", "")


@pytest.mark.parametrize("text", ["how transformers work", "new to python? five decorator tricks"])
def test_evergreen_topics_get_no_window(text):
    window = typed(text)
    assert window["start"] == "" and window["oldest"] == "" and window["phrase"] == ""


def test_today_without_a_news_word_means_now_not_a_date():
    window = typed("AI will change healthcare today")
    assert window["strict"] is False
    assert window["oldest"] == ""


def test_an_rss_or_url_story_gets_no_window_but_keeps_its_date():
    window = tw.resolve({"id": "u", "title": "The latest AI news this week", "source_name": "web",
                         "published_at": "2026-09-24T14:08:38.298Z"}, NOW)
    assert window["start"] == "" and window["oldest"] == "" and window["strict"] is False
    assert window["story_published_on"] == "2026-09-24"


# --- fact_dates -----------------------------------------------------------


def _day(y, m, d):
    return (date(y, m, d), date(y, m, d))


@pytest.mark.parametrize("text, spans", [
    ("2026-09-22", [_day(2026, 9, 22)]),
    ("published 2026-09-24T14:08:38Z", [_day(2026, 9, 24)]),
    ("OpenAI announced it on March 17, 2026.", [_day(2026, 3, 17)]),
    ("It shipped on 17 March 2026.", [_day(2026, 3, 17)]),
    ("Released Sep. 2, 2026 and updated 25th September 2026",
     [_day(2026, 9, 2), _day(2026, 9, 25)]),
    ("the September 2026 launches", [(date(2026, 9, 1), date(2026, 9, 30))]),
    ("2026-09", [(date(2026, 9, 1), date(2026, 9, 30))]),
    ("compromised between 11 and 13 July", [_day(2026, 7, 13)]),
    ("Gemini 3.8 Flash costs $0.75 per 1 million tokens (2025-26 pricing)", []),
    ("they may 5 times reconsider", []),
])
def test_fact_dates_reads_the_formats_research_writes(text, spans):
    assert tw.fact_dates(text, TODAY) == spans


# --- window_problems ------------------------------------------------------


def test_the_march_brief_for_this_week_is_rejected():
    window = typed("this week major ai updates like new model launches and the comparative open weight models")
    problems = tw.window_problems(RUN_3C41_FACTS, window)
    assert problems[0] == "no fact is dated inside 20 September 2026 to 26 September 2026"
    assert "fact 1 is dated 17 March 2026, before 20 September 2026" in problems
    assert any(p.startswith("facts 2, 4, 5") and p.endswith("have no date") for p in problems)
    # Even the last attempt's relaxed rule cannot pass a brief with no current fact.
    assert tw.window_problems(RUN_3C41_FACTS, window, relaxed=True) == [
        "no fact is dated inside 20 September 2026 to 26 September 2026"]


def test_the_september_brief_flags_its_early_september_and_august_facts():
    window = typed("AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this week September 2026")
    problems = tw.window_problems(RUN_79E4_FACTS, window)
    assert "fact 1 is dated 2 September 2026, before 20 September 2026" in problems
    assert "fact 12 is dated 10 August 2026, before 20 September 2026" in problems
    assert any("only 1 of the 12 facts" in p for p in problems)
    # "pricing would change on January 1, 2027" is a schedule, not staleness.
    assert not any("2027" in p for p in problems)
    assert tw.window_problems(RUN_79E4_FACTS, window, relaxed=True) == []


def test_the_25_september_brief_passes_once_its_facts_are_dated():
    window = typed("AI MODELS RACE ON SPEED AND OPEN WEIGHTS on this 25th September 2026")
    assert any("have no date" in p for p in tw.window_problems(RUN_AA99_FACTS, window))
    dated = [dict(fact, date="2026-09-22") for fact in RUN_AA99_FACTS[:8]]
    dated += [dict(RUN_AA99_FACTS[8]), dict(RUN_AA99_FACTS[9], date="2026-09-24")]
    dated += [dict(RUN_AA99_FACTS[10], background=True), dict(RUN_AA99_FACTS[11])]
    assert tw.window_problems(dated, window) == []


def test_background_facts_are_not_flagged_and_do_not_count_against_the_share():
    """run-3c41 asked for launches "and the comparative open weight models": the
    comparison facts are background, and labelling them so made the brief fail."""
    window = typed(RUN_3C41_TOPIC)
    old = {"fact": "Mistral Small 4 launched on March 16, 2026.", "date": "2026-03-16", "background": True}
    new = {"fact": "Xiaomi released MiMo-V2.6.", "date": "2026-09-22"}
    assert tw.window_problems([new, old], window) == []
    assert tw.window_problems([new, old, old], window) == []
    assert tw.window_problems([new, new, new, old, old, old, old], window) == []
    stale = {"fact": "GPT-5.4 mini launched on March 17, 2026.", "date": "2026-03-17"}
    assert tw.window_problems([new, stale, stale, old], window) == [
        "only 1 of the 3 facts not marked background are dated inside 20 September 2026 to "
        "26 September 2026; at least half must be",
        "fact 2 is dated 17 March 2026, before 20 September 2026",
        "fact 3 is dated 17 March 2026, before 20 September 2026",
    ]
    # Labelling everything background does not make a brief current.
    assert tw.window_problems([old], window, relaxed=True) == [
        "no fact is dated inside 20 September 2026 to 26 September 2026"]


def test_a_soft_news_window_flags_only_facts_older_than_its_limit():
    window = typed("trending AI news")
    stale = {"fact": "Mistral Small 4 launched on March 16, 2026."}
    undated = {"fact": "Mistral Small 4 has 119B parameters."}
    fresh = {"fact": "MiMo-V2.6 is open source.", "date": "2026-09-22"}
    assert tw.window_problems([fresh, undated], window) == []
    assert tw.window_problems([fresh, stale], window) == [
        "fact 2 is dated 16 March 2026, before 27 August 2026"]
    assert tw.window_problems([stale, undated], window, relaxed=True) == [
        "no fact is dated on or after 27 August 2026"]


@pytest.mark.parametrize("window", [
    None,
    {},
    typed("how transformers work"),
    typed("latest iPhone camera features"),
])
def test_no_window_or_an_explainer_is_never_checked(window):
    assert tw.window_problems(RUN_3C41_FACTS, window) == []


# --- prompt text ----------------------------------------------------------


def test_the_context_note_states_the_utc_date_and_the_window():
    window = typed("this week major ai updates")
    note = tw.context_note(window, TODAY)
    assert note.startswith("Today is Saturday, 26 September 2026 (UTC).")
    assert '"this week", which means 20 September 2026 to 26 September 2026' in note
    assert "request was made" not in note
    assert "{" not in note and "}" not in note


def test_a_resumed_run_keeps_the_request_day_in_the_note():
    note = tw.context_note(typed("AI news this week"), date(2026, 9, 28))
    assert "Today is Monday, 28 September 2026 (UTC)." in note
    assert "The request was made on 26 September 2026." in note
    assert "20 September 2026 to 26 September 2026" in note


def test_the_note_for_a_story_without_a_window():
    window = tw.resolve({"id": "u", "title": "Story", "source_name": "web",
                         "published_at": "2026-09-24T14:08:38Z"}, NOW)
    note = tw.context_note(window, TODAY)
    assert "The story was published on 24 September 2026." in note
    assert "no date window applies" in note
    braced = dict(typed("trending AI news"), phrase="{news_item}")
    assert "{" not in tw.context_note(braced, TODAY)


def test_search_hint_and_range_describe_the_window():
    window = typed("this week major ai updates")
    assert tw.describe_range(window) == "20 September 2026 to 26 September 2026"
    assert "20 September 2026 to 26 September 2026" in tw.search_hint(window)
    assert "27 August 2026" in tw.search_hint(typed("trending AI news"))
    assert tw.search_hint(typed("how transformers work")) == tw.describe_range(None) == ""
    assert tw.window_rule(window) == 'inside 20 September 2026 to 26 September 2026 ("this week", UTC)'


def test_the_time_section_carries_only_the_optional_placeholder():
    assert "{time_context?}" in tw.TIME_SECTION
    assert tw.TIME_SECTION.count("{") == tw.TIME_SECTION.count("}") == 1
    assert tw.with_time_context("Base prompt.\n\n").startswith("Base prompt.\n\n## Today's date")
    assert "never call a background fact new" in tw.TIME_SECTION


# --- more ways of naming a period ------------------------------------------


@pytest.mark.parametrize("text, start, end", [
    ("AI launches since September 20", "2026-09-20", "2026-09-26"),
    ("AI news after September 20", "2026-09-20", "2026-09-26"),
    ("AI releases from 20 September onwards", "2026-09-20", "2026-09-26"),
    ("AI news since August", "2026-08-01", "2026-09-26"),
    ("AI news from December 5", "2025-12-05", "2026-09-26"),
    ("biggest AI stories of September", "2026-09-01", "2026-09-26"),
    ("AI news past 24 hours", "2026-09-25", "2026-09-26"),
    ("AI launches last 3 days", "2026-09-24", "2026-09-26"),
    ("AI releases in the last 2 weeks", "2026-09-13", "2026-09-26"),
    ("AI news this weekend", "2026-09-25", "2026-09-26"),
    ("AI news over the past few days", "2026-09-24", "2026-09-26"),
    ("AI news this morning", "2026-09-25", "2026-09-26"),
    # No news word, but no advice either: this pipeline makes news.
    ("AI this week", "2026-09-20", "2026-09-26"),
    ("the week in AI", "2026-09-20", "2026-09-26"),
    ("AI updates of the week", "2026-09-20", "2026-09-26"),
])
def test_more_ways_of_naming_a_period_become_strict_windows(text, start, end):
    window = typed(text)
    assert (window["start"], window["end"], window["strict"]) == (start, end, True), window


def test_since_a_day_counts_the_days_after_it():
    """"since September 20" was read as the week up to the 20th."""
    window = typed("AI launches since September 20")
    facts = [{"fact": "Xiaomi released MiMo-V2.6.", "date": "2026-09-22"},
             {"fact": "Liquid AI published an LFM2.5-VL draft model.", "date": "2026-09-24"}]
    assert tw.window_problems(facts, window) == []
    assert "20 September 2026 to 26 September 2026" in tw.context_note(window, TODAY)


def test_a_date_after_a_window_that_ended_before_the_request_is_named():
    """It was called undated, which the model cannot fix: it gave the date."""
    window = typed("AI launches on 14 September 2026")
    assert (window["start"], window["end"]) == ("2026-09-08", "2026-09-14")
    fresh = {"fact": "Mistral shipped a model.", "date": "2026-09-12"}
    late = {"fact": "Xiaomi released MiMo-V2.6.", "date": "2026-09-22"}
    assert tw.window_problems([fresh, late], window) == [
        "fact 2 is dated 22 September 2026, after 14 September 2026"]
    # A schedule past the request day is still ignored.
    schedule = {"fact": "Prices change on January 1, 2027.", "date": "2026-09-12"}
    assert tw.window_problems([fresh, schedule], window) == []


@pytest.mark.parametrize("text, oldest", [
    ("current AI news", "2026-08-27"),
    ("AI news right now", "2026-08-27"),
    ("weekly AI roundup", "2026-08-27"),
    ("what's happening in AI", "2026-08-27"),
    ("current state of quantum computing", ""),
])
def test_words_for_now_make_a_soft_window(text, oldest):
    window = typed(text)
    assert (window["strict"], window["start"], window["oldest"]) == (False, "2026-09-13", oldest)


@pytest.mark.parametrize("text", [
    "GPT 5 august launch recap",
    "Pixel 10 august drop review",
    "android 16 march security patch explained",
    "iOS 18 june update features",
    "top 10 march madness moments",
])
def test_version_and_rank_numbers_before_a_month_are_no_dates(text):
    """These became strict one-week windows no fact could meet, and the run stopped."""
    window = typed(text)
    assert window["start"] == "" and window["oldest"] == "" and window["strict"] is False


@pytest.mark.parametrize("text, start", [
    ("5 habits to build this month", "2026-09-01"),
    ("productivity tools to try this week", "2026-09-20"),
    ("how to use ChatGPT this week", "2026-09-20"),
])
def test_a_period_word_on_an_evergreen_topic_is_never_date_checked(text, start):
    """Its facts carry no dates, so a strict window stopped the run for good."""
    window = typed(text)
    assert (window["start"], window["end"], window["strict"], window["oldest"]) == (
        start, "2026-09-26", False, "")
    habit = {"fact": "Habit stacking pairs a new habit with an existing one.", "date": ""}
    assert tw.window_problems([habit], window) == []
    assert tw.window_problems([habit], window, relaxed=True) == []


def test_a_yearless_date_still_to_come_is_last_years():
    assert tw.fact_dates("OpenAI launched X on December 5", TODAY) == [_day(2025, 12, 5)]
    window = typed("this week AI launches")
    problems = tw.window_problems([{"fact": "OpenAI launched X on December 5.", "date": ""}], window)
    assert "fact 1 is dated 5 December 2025, before 20 September 2026" in problems
    assert not any("no date" in problem for problem in problems)


def test_a_year_alone_places_a_fact_before_a_window_never_inside_it():
    window = typed("AI news this week")
    assert tw.fact_dates("2017", TODAY) == [(date(2017, 1, 1), date(2017, 12, 31))]
    new = {"fact": "Liquid AI published a draft model.", "date": "2026-09-24"}
    old = {"fact": "The Transformer was introduced.", "date": "2017"}
    vague = {"fact": "Xiaomi released MiMo-V2.6.", "date": "2026"}
    assert tw.window_problems([new, old, vague], window) == [
        "only 1 of the 3 facts not marked background are dated inside 20 September 2026 to "
        "26 September 2026; at least half must be",
        "fact 2 is dated 2017, before 20 September 2026",
        "fact 3 gives only a year",
    ]
