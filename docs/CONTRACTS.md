## Telegram broadcasts

Telegram connections are stored as independently encrypted bot entries in the
existing `app_config.telegram` value. Legacy single-bot values are preserved and
upgraded on the next write. Database row locking prevents concurrent connections
from overwriting one another. Reconnecting a bot updates that bot; disconnecting
by bot id removes only that destination.

Each existing Telegram tool makes one deterministic broadcast to all bots, using
the same generated payload and each bot's own token/chat pairing. There are no
per-bot LLM calls. Every destination is attempted, with up to four concurrent
sends. A partial failure is reported instead of claiming complete delivery.

## Optional Instagram delivery

Instagram is optional. After QA passes, runs with an empty `account_id` deliver
all `bundle.ordered_artifacts` and the full caption to every connected Telegram bot and transition
directly to `done`, storing `publish_result.status = "delivered"`. They create
no pending review and never call the Instagram publisher. Delivery failure must
not mark the run complete. Artwork for these runs omits Instagram identity marks.

Runs bound to an Instagram account retain the human approval gate before
publishing. The run's chosen account is never replaced with another account at
publish time. Multiple accounts are supported with independently encrypted
tokens. New account connections use `/settings/instagram/token` only.

For run creation, omitted/null `account_id` chooses a usable default when one
exists, otherwise Telegram only. An explicit empty string selects Telegram only
even if accounts are connected; an invalid explicit account is refused.

# Carousel Factory - Build Contracts

The binding spec for every file in this repo. The C4 model
(`architecture/carousel.c4`) is the architecture authority; this file pins the
code-level contracts. If a task conflicts with this file, this file wins.

## Global constraints

- Python 3.11+ (dev venv: `.venv`, Python 3.13). Windows-friendly paths
  (always `pathlib`, never hard-coded `/tmp` - use `settings.workdir`).
- NO git operations anywhere. Never commit, never push.
- Secrets ONLY via `app.config.settings` (env-driven). Never hard-code keys.
- Every agent lives in its own file under `app/agents/`, exposing a builder
  function `build_<name>_agent() -> BaseAgent`-compatible object.
- Agent display/routing names come from `app/state.py` constants - never
  free-typed strings.
- All session-state access uses the `K_*` keys and `get_model`/`set_model`
  helpers from `app/state.py`. State values must stay JSON-serializable.
- Instruction text for each LlmAgent loads from `skills/agents/<name>.md` via
  `app.config.agent_instructions(name)`, with a sensible inline fallback
  string if the file is missing. (The Learner agent edits those files - this
  is how feedback permanently updates the harness.)
- Type hints + docstrings everywhere. No `TODO`-only stubs: every function is
  implemented. External calls get explicit timeouts and raise-for-status.

## ADK API cheatsheet (VERIFY against `.venv/Lib/site-packages/google/adk/`)

The installed `google-adk` package is the ground truth. Before using any ADK
symbol, confirm it exists by reading the installed source. Expected surface:

```python
from google.adk.agents import LlmAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.models.lite_llm import LiteLlm            # OpenAI models
from google.adk.tools import FunctionTool, LongRunningFunctionTool, ToolContext
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.artifacts import BaseArtifactService
from google.adk.memory import BaseMemoryService
from google.genai import types  # Content/Part for messages & function responses
```

- `LlmAgent(name=..., model="gemini-2.5-pro" | LiteLlm(model="openai/gpt-5"),
  instruction=..., tools=[...], output_schema=PydanticModel, output_key="...")`
  - `output_key` writes the final response into `session.state[output_key]`.
- Custom orchestrator: subclass `BaseAgent`, implement
  `async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]`
  and drive children with `async for event in child.run_async(ctx): yield event`.
  Declare children in `sub_agents=[...]` so `adk web` renders the agent graph.
- Tools are plain functions (type-hinted, docstring) wrapped in `FunctionTool`;
  they may accept `tool_context: ToolContext` to touch `tool_context.state`
  and `await tool_context.save_artifact(filename, types.Part(...))`.
- If a signature differs in the installed version, FOLLOW THE INSTALLED
  VERSION and note the difference in your report.

## The orchestrator state machine (app/orchestrator.py)

The review pause ends the invocation; the resume is a NEW invocation. The root
agent is therefore a re-entrant state machine over `state[K_PHASE]`:

| phase      | action                                                                            | next |
|------------|-----------------------------------------------------------------------------------|------|
| (missing)  | init run: set K_RUN_ID, K_REWORK_ROUND=0, K_REVIEW_ROUND=0                        | generate |
| generate   | research → planner → first_page_visual → phrasing → template_design → cta          | qa |
| qa         | stitch_verify (assembles Bundle + QAReport)                                        | review |
| review     | review_dispatcher: sends mail, calls `await_human_review` (LongRunningFunctionTool) - invocation PAUSES here. On resume the tool response carries the verdict; dispatcher writes K_VERDICT. approved → publish; rejected → rework | publish / rework |
| rework     | learner (store feedback) → feedback_router (writes K_REWORK_PLAN) → re-run ONLY the target agents (subset of REWORKABLE_AGENTS), passing K_REWORK_FEEDBACK; increment K_REWORK_ROUND (cap: settings.max_rework_rounds) | qa |
| publish    | learner (store optional feedback) → publisher (IG publish + confirmation mail)     | done |
| done       | emit final summary event, stop                                                     | - |

Rework targeting: `"the first visual is not good"` → only `first_page_visual`
re-runs; then qa → review again with the replaced piece. If the router targets
`planner`, downstream agents whose inputs changed re-run too (planner implies
full regenerate of dependents; `research` implies planner and therefore a full
regenerate on the corrected facts) - the router's `reasons` say why.

Repair retry: when a step's hand-off fails `validate_output`, the step runs
once more with a `Repair the <name> output: ...` note appended to
K_REWORK_FEEDBACK, the key every agent prompt already shows. Every other agent
reads that key as the reviewer's words, so the note belongs to that retry
alone: it is stripped at the step's checkpoint, before the run stops after a
second failure, and whenever a step is prepared (a run that stopped mid-retry
cannot carry it into a resume). The reviewer's own feedback is never changed.

## Review resume protocol (web_api/routes_runs.py ↔ dispatcher)

1. Dispatcher tool `send_review_message(...)` posts the preview album to
   Telegram with one button: `{PUBLIC_BASE_URL}/tasks/{run_id}?tab=review`,
   the console's own review screen. There is no anonymous approval surface -
   the standalone `/review-api` Approve/Reject pages were deleted, because a
   URL that publishes to Instagram with no sign-in is a permanent, forwardable
   publish button. The verdict is submitted by `POST /api/runs/{id}/verdict`,
   which requires a session and records who decided.
2. Dispatcher then calls `await_human_review()` - a `LongRunningFunctionTool`
   returning no immediate result → ADK pauses; runner invocation ends with the
   pending function_call id (persist it in state via the tool callback or db).
3. Review API endpoints render a CONFIRM page (mail scanners prefetch GETs!).
   Approve: optional feedback textarea. Reject: feedback REQUIRED, form asks
   "what exactly is not good? (first visual / texts / design / CTA / other)".
4. On submit the API loads the session (same DatabaseSessionService database),
   builds `types.Content` with a `types.Part(function_response=...)` matching
   the pending call id/name, payload `{"status": "approved"|"rejected",
   "feedback": "..."}`, and calls `Runner.run_async` on the same
   app_name/user_id/session_id to resume the pipeline.

## Cover picture ladder

Every cover shows a real sourced picture. The drawn plain background is a last
resort that is refused while any picture is still possible, and it never
reaches a reviewer without a warning; neither does a picture the automatic
picture check did not approve. The cover is never AI-generated by us; official
artwork the story's own organisation published is sourced media.

1. `find_source_clip`: the story's own media, linked pages, research pages and
   one trend search (one attempt, 60 s timeout, windowed, run while the pages
   load), with CDN variants rewritten to cover size and off their logo paths
   and tiny images dropped before they are offered.
2. `find_reference_photo`: free-licensed, credited Wikimedia photos of the
   story's people, organisation, leader or place. When no source picture is
   found it is fetched automatically.
3. The plain background (`create_placeholder_background`).

Budgets per cover step: 3 `find_source_clip` calls (an identical query is
served from a cache for free; the result says `calls_left`; pages scraped by
an earlier call are cached in `temp:cover_page_cache`), 3
`find_reference_photo` calls and 2 vision checks kept for reference photos.
Vision checks are otherwise capped at 6 per invocation, plus 1 for a video
downloaded from a held-back `video_url`; that cap and the held-video look are
deliberately not reset for a QA re-run (they cap billed calls per
invocation). URLs that failed to download are remembered, and so is any URL a
download of which was judged tier 0 (session state, derived from the
verdicts): neither is offered by `find_source_clip` or `find_reference_photo`
again, and `download_image` and `download_and_trim` refuse a judged URL (the
reviewer's own link excepted). URLs are compared by `_url_key`
(percent-encoding, utm_* tags and the scheme ignored). The per-step
counters are `temp:` state keys reset by a `before_agent_callback`, because
ADK 2.7.0 keeps `temp:` keys across child agents within one invocation, and a
QA re-run of the cover would otherwise start with its budget spent.

Time: the orchestrator stamps `K_COVER_DEADLINE` (epoch seconds, 900 s after
the step starts; the step limit is 1200 s). Every tool's thread is awaited for
at most its cap and what is left (search 150 s plus 15 s slack, reference
lookup 40 s, image download 45 s, video trim 120 s, picture check 60 s). With
less than 300 s left, `find_source_clip`, `find_reference_photo` and
`download_and_trim` refuse and say to build, and `build_cover` no longer
waits for the reference rung (refusals a and c below). When the step still
ends without a valid cover (timeout, crash, or two attempts without one), the
orchestrator runs `first_page_visual.ensure_cover(tool_context, budget_s=240)`
without the model: best_so_far, then any other downloaded picture (images
first), then a Wikimedia reference photo, then the drawn background. Only
when even that fails does the step fail as before.

`inspect_cover_media` returns the judge's `kind` and `tier`, `official` and
`best_so_far` (`{path, url, kind, tier, score, verdict, origin, best_start_s,
retrim_from}`): the inspected file that still exists, has tier 1 or higher,
was not cut from tier-0 media (which `build_cover` refuses), was not turned
down by a human reviewer, and ranks best by (`verdict == 'use'`, score >= 3,
tier, score). The agent builds best_so_far directly when its verdict is `use`
or it is tier 3 and scored 4 or more; otherwise it tries the reference rung
first. `build_cover` cuts a video that does not open on its judged moment (an
untrimmed source, or download_and_trim's clip) at `best_start_s` itself, so the
approved frame is the cover's first frame and poster; a clip `retrim_clip`
cut elsewhere on purpose is left alone.

`build_cover` refuses, in this order:

1. (a) the plain background while any inspected picture of tier 1 or higher,
   or any downloaded picture not judged tier 0, exists, and until
   `find_reference_photo` has been tried;
2. (b) media a tier-0 verdict covers (for ai_art the error names
   `ai_illustration`): a verdict covers the judged file and every clip cut
   from it; an ai_art verdict also covers every file the judged one was cut
   from and any other download of the same URL; an unusable one covers the
   files it shares its footage with (download_and_trim's clip and the source
   it kept, but not the source of a moment `retrim_clip` picked) and their
   URL. The same rule keeps such media out of best_so_far, the downloaded
   pictures the plain background waits for, and `ensure_cover`;
3. (c) a tier-1 text graphic scoring under 3, until the reference rung was
   tried and no picture of tier 2 or higher exists.

Anything else is allowed, including media that was never inspected, and media
downloaded from an image link in the rework feedback is never refused (the
reviewer chose it). The resulting preference is: real or official pictures
(tier 3/2), text graphics scoring 3 or more, reference photos, weaker text
graphics, the plain background. A `CoverSpec` validation error returns
`ok: false` instead of raising. A human rejection that finds fault with the
picture moves the media the last cover was built from (`cover_built_media`)
into `cover_reviewer_rejected`, which leaves best_so_far: any link, or one
clause (split at . ! ? ; , and but/however/although/though) naming the
picture (image, photo, pic, video, clip, footage, background, visual, media)
with a fault word (wrong, bad, different, replace, change, another, instead,
better, not, n't, ...) and no praise or keep that is not negated ("Love the
photo, keep it" and "not good" read as they should). A clause about the crop,
zoom, framing or moment of the clip keeps the picture: that is fixed on it.

`CoverSpec` (app/schemas.py) records the outcome:

- `drawn_background: bool` is True when the media file is `PLACEHOLDER_NAME`.
  It is the only marker: never write the word "placeholder" into a CoverSpec
  or Bundle text field, because the readable-text rule rejects it.
- `picture_verdict` (`use`, `reject`, `unchecked` when never inspected, `''`
  for the drawn background and for covers from before the field),
  `picture_kind`, `picture_tier`, `picture_score`, `picture_from_reviewer`
  and `share_alike_skipped` (free Wikimedia photos left out only for a
  share-alike licence) describe the picture for the QA gate and the review.
- `source_credit` (checked like published text) and `source_origin`
  (`wikimedia` for a reference photo, or for a Wikimedia file found on a page
  once `reference_photos.file_credit` credited it) come from the picture's
  provenance, as does `source_media_url`. A Wikimedia file whose licence is not
  on the allowlist is not downloaded.
- `PublishedTextModel.text_rules_exempt` names fields that are never published.
  `CoverSpec` exempts `video_artifact`, `poster_artifact`, `source_media_url`,
  `picture_verdict` and `picture_kind`: a TechCrunch image URL with an em dash
  in its file name once lost a finished cover. A field holding a nested
  `PublishedTextModel` is not re-checked by its parent, since the nested model
  checked itself with its own exemptions.

QA gate (stitch_verify): a drawn-background cover, or one whose picture
`cover_notice.weak_picture_reason` flags (a text graphic scored under 3, a
rejected picture, an unchecked one; never the reviewer's own image), is a
critical issue routed to `first_page_visual` alone (no dependents re-run),
asking it to call `find_reference_photo` with broader subjects (the rework
instructions for both the plain background and an unapproved picture skip
`find_source_clip`, whose billed search would find the same pages), and sets the
persisted session flag `cover_picture_retried`. The message never names
research or planner, because `_target_for_issue` routes to the first agent
name it finds. After that one retry, or when `K_QA_ROUND` has reached
`max_qa_rounds` (a critical issue then would stop the run), it is a major
note: QA passes and the reviewer decides. When a credit is set and
`source_origin` is `wikimedia`, the Bundle caption ends with
`Cover photo: <credit>`, added once; `K_COPY` is never changed.

Licences (app/tools/reference_photos.py) are an allowlist: public domain
(CC0, PD-*), CC BY (any version, port or IGO), OGL, GODL and "Attribution";
CC BY-SA only when `settings.cover_allow_share_alike`
(`COVER_ALLOW_SHARE_ALIKE`, off by default because the owner has not decided;
the review notes when such photos were left out of a weak cover). GFDL, GPL,
FAL, NC, ND and anything unrecognised are never used. An attribution credit
ends with "(cropped, text added)".

Review surfaces all use `app/cover_notice.py`. The Telegram review message puts
the notices (`COVER_WARNING`, `PICTURE_WARNING`, `SHARE_ALIKE_NOTE`) under the
title; `GET /api/runs/{id}/artifacts` adds `cover.drawn_background`,
`cover.credit` and `cover.notices`, and the console shows the notices in a
warning banner above the cover and the credit under it; the account-free
review progress line appends the notices.

## Time window (typed topic runs)

No model knows today's date on its own, so a request for "this week" once
returned March launches in September. `app/time_window.py` resolves the typed
request into a UTC window once, at init, stored as `K_TIME_WINDOW`:

    {"requested_on": "YYYY-MM-DD", "phrase": str, "start": "YYYY-MM-DD" or "",
     "end": "YYYY-MM-DD" or "", "strict": bool, "oldest": "YYYY-MM-DD" or "",
     optional "story_published_on": "YYYY-MM-DD"}

- Only typed topic runs (`source_name == 'adhoc'`) get a window. No window
  means `start == ''` and `oldest == ''`. A recency word ("latest",
  "trending", "current", "right now", "weekly") makes a soft window: `strict`
  false, `start` 13 days before the request date, `end` the request date,
  `oldest` 30 days back for a news-shaped request.
- Strict windows: "this week", "the week in" and "of the week" (D-6..D),
  "last week" (D-13), "yesterday" (D-2), "this morning" and "last night"
  (D-1), "this weekend" (from the Friday before the latest Saturday),
  "past/last/previous N or a few hours, days, weeks or months" (24 hours =
  D-1, N days = D-(N-1), N weeks = D-(7N-1), N months = D-30N), "this/last
  month", a named day (the week up to it), a named month (that month up to D;
  with no year, its latest occurrence on or before D, read only after
  of/in/from/since/during/for ...), and "since", "after", "from" or
  "starting" a day or month, or "... onwards" (from it up to D). A period
  word in a request that reads as advice (habits, tips, how to, things to try
  ...) and names no event (news, launches, updates, what happened, a recency
  word ...) is only preferred: "5 habits to build this month" gets its period
  as a soft window with no age limit, while "AI this week" stays strict. In a
  request a yearless "5 august" is a date only after a date word (on, since,
  from, the, of, to, and ...) or with an ordinal ("25th"): "GPT 5 august
  launch recap" and "top 10 march madness moments" have no window. A day or
  month with no year that would lie more than a day after D is last year's
  (requests and facts alike).
- Before research, planner and phrasing run, the orchestrator writes
  `K_TIME_CONTEXT` (today's date plus the window, shown as `{time_context?}`).
  `search_web` always states today's date (UTC) and the window, and the cover's
  trend search uses the window too.
- `ResearchFact` has `date` (YYYY-MM-DD, YYYY-MM or YYYY, when the source
  says it happened or was announced; empty when it gives none) and
  `background` (older context, never presented as new). `save_research_brief`
  reads "N/A"/"unknown" as empty, an unambiguous "22/09/2026" and the one year
  of "Q3 2026"; any other unreadable date is stored empty and is an error
  (listing every such value) only under a strict window.
  `save_research_brief` and `validate_output('research')` apply the same
  `window_problems` rule: in a strict window every fact not marked background
  is dated with a day or month (a year alone only shows a fact is older), none
  is dated before the window (or, for a window that ended before the request
  day, after it: "dated X, after the window", never "no date") and at least
  half of the facts not marked background are inside it (background facts
  never count, so the comparison context a request asks for cannot fail a
  brief); a soft window with an age limit flags facts older than `oldest`.
  The final repair attempt and human rework use the relaxed rule (at least
  one fact inside): the orchestrator sets `K_RESEARCH_RELAXED`
  (`research_relaxed_window`) for the research step under rework and on its
  retry, and the save tool reads it. The brief's media is merged into
  `media_urls` whenever the relaxed rule passes. A resumed checkpoint is not
  re-checked. A soft window with no age limit is never checked. The repair
  retry carries the exact reason; a run that still has no fact inside the
  window stops with a `TimeWindowError` message saying the dates are fixed for
  the run (resume to search them again, or start a new run). The research
  agent's `search_web` allows 7 billed searches per attempt
  (`temp:research_searches`, reset by a `before_agent_callback`). Planner and
  phrasing may call something "this week" or "new" only when its date says
  so.
- URL runs record the article's own publication date (JSON-LD or meta tags) as
  `story_published_on`.

## File map & responsibilities

| file | must expose |
|---|---|
| app/tools/media_tools.py | `find_source_clip(news: dict, search_query="", research_sources=None, research_media=None, *, time_window=None, subjects=None, page_cache=None, budget_s=None, provenance=None) -> dict` scans media_urls, including page-form media candidates, plus the source page, BODY-linked pages and the lead media of the articles the research brief cites (`research_sources`, origin `research_page`; boosted only in a headline run with no source_url). `research_media` is the brief's `media_candidates`: save_research_brief merges them into media_urls, and any media_urls entry listed there is ranked as `research_page`, not as the story's own. It runs one live trend-aware visual search (`search_web(query, window=time_window, timeout_s=60, attempts=1)`: one attempt, 60 s, inside the run's time window when it has one, started while the pages are scraped side by side) and ranks candidates by topicality, prominence, trusted-source affinity, and generic or unverified-blog penalties. Known CDN variants are rewritten to cover size and off their logo paths before ranking (`upgrade_image_variant(url) -> str`: BBC ichef `/news/<w>/`, `/ace/standard/<w>/`, `/ace/ws/<w>/` to 2048 and `/news/<w>/branded_news/` to `/news/2048/cpsprodpb/`; ABC wcms `impolicy=wcms_watermark_news` to `wcms_crop_resize`, a width and height pair under 1080 scaled to 1600 wide, a width alone left as is; Guardian `i.guim.co.uk/img/media/` to the unsigned `width=1600&dpr=1&s=none&crop=none`, and `/img/uploads/` avatars dropped; generic `w`/`width`/`resize` under 1080 px, `dpr` counted, scaled to 1600; a signed URL, including GCS `X-Goog-Signature`/`X-Goog-Credential`, S3, Akamai `hdnts`/`hdnea`/`__token__`, Alibaba OSS, Tencent COS and a bare `auth` or `token`, is never rewritten). The ichef path width and `dpr` count in the advertised size. `provenance`, when given, is filled in place for every candidate: `{url: {context_url, original}}`, the page it was found on and the URL as found before the rewrite ('' when not rewritten; of several sizes rewritten to one variant, the largest), so the caller knows a video's page and can fall back to the original; the C1 keys are unchanged. Page GETs are streamed (Content-Type read first, 3 MB and 25 s cap, `.pdf` never fetched); `page_cache` holds this step's scrapes so a re-call fetches only new pages; a trend page already scraped as the story's own is not scraped again; `budget_s` (default 150 s) bounds the whole call. For every candidate origin, page furniture (icons, logos, avatars, theme images, 1x1 pixels, matched on the image's own file name or furniture folders only), thumbnails, SVGs, tracking hosts and images whose own file name marks them AI-generated are dropped, and a free Range-GET probe (`probe_image_size(url) -> (w, h)`, `(0, 0)` when unknown) drops images `image_fits_cover(w, h)` rejects before they are offered; size variants of one picture collapse to its best variant. URL-only news titles are converted into a readable subject query. The video search and its title check use the title plus `subjects`, capped at about 160 characters, never the scraped page. Contract C1: every result has exactly the keys `found`, `url`, `is_video`, `duration_s`, `origin`, `image_url`, `image_origin`, `image_candidates` (up to five ranked, distinct stills, each `{url, origin, score, reason, context_url, width, height}`), `image_first`, `video_url`, `video_duration_s`, `video_origin`, `trend_search` and `note`; a `found=false` note names `find_reference_photo`, never the plain background. A video from a third-party origin (`web_search`, `trend_search`, `research_page`) must have a title naming the topic and must not be a live, upcoming or past live stream or longer than two hours (a live or upcoming stream is refused from any origin), and when any ranked image comes from a story page (origin in media_urls/media_page/source_page/body_url/body_page/research_page) an image is the pick: `url=image_url`, `is_video=False`, `origin=image_origin`, `image_first=True`, and the held-back video goes in `video_url`/`video_duration_s`/`video_origin`; otherwise those are `""`/`0.0`/`""` and `image_first=False`. `next_scene_cut(path, start_s, window_s) -> float|None` is the offset of the first hard cut after `start_s` (any cut after the first frame counts). `download_image` tries the cover-size variant, then the original URL unless the URL itself says it is too small, streams the body (25 MB, 40 s cap), and rejects tiny/extreme-aspect assets (the error text keeps "unsuitable") so the agent tries the next ranked result. `download_and_trim(url, max_s=None, min_s=None) -> str(path)` defaults to the 4-15 s cover window. `placeholder_background(workdir) -> str(path)` is the non-AI last resort; its file name is `PLACEHOLDER_NAME` (`placeholder-bg.png`). `WIKIMEDIA_USER_AGENT` names the project URL, never a person's email. `compose_cover(media_path, title, highlight, is_video: bool)` composites the overlay plus a fixed 128 px, up-to-three-line cover headline with solid `#8FB832` emphasis, then outputs 1080x1350 mp4 plus poster. ffmpeg runs via `settings.ffmpeg_bin`; yt-dlp uses its Python API. |
| app/tools/cover_vision.py | Contract C2. `contact_sheet(media_path, is_video, workdir="") -> Sheet` lays out six numbered frames of a video (or the still). `judge_cover_media(sheet, story, hook, client=None, *, provenance="", official="") -> dict` makes one small vision call and returns `best_frame, score (0-10), verdict ('use'/'reject'), problems, usable_frames, focus, clean, reason, kind`; `_parse_verdict(text, frame_count, official="")` keeps that shape for any model answer. `kind` is one of `KINDS = ('real_photo', 'official_visual', 'text_graphic', 'ai_art', 'unusable')` and `tier_for(kind, origin="", problems=(), official="")` ranks it: real_photo 3 (2 when the origin is `wikimedia`), official_visual 2 only from an exact official source and 1 otherwise, text_graphic 1, ai_art, unusable and unknown 0; `generic_stock` caps any tier at 1. `kind_from_problems(problems, verdict='reject')` derives a kind for verdicts without one: an unusable problem only decides a rejection, and a rejection naming no known problem is unusable. The judge sees where the image was found (`provenance`) and `official`, computed in code by `official_source(urls, subjects, publishers=(), sites=()) -> str` on the story's subject NAMES: `OFFICIAL_EXACT` when the host is a subject's Wikidata official website (`sites`, P856) or its name part and domain spell a subject's whole name (liquid.ai for Liquid AI, cms.mistral.ai for Mistral AI; an `.ai` domain spells the company form, so mistral.ai for Mistral too) with no known official website of that name on another host; `OFFICIAL_FUZZY` when the name part alone is a subject's name on some domain (xiaomi.eu, liquid.com, medicare.gov, and also openai.com until P856 confirms it) or it only resembles one (two subject words run together, a subject plus cdn/static/assets/media/ai/labs/hq, one word of a longer name); `''` otherwise, including a host whose name a known official website uses on another domain (liquid.com when the P856 is liquid.ai); news, platform and fan hosts (today, time, master, week, insider ...) never match. `official_level(value)` reads stored bools as exact. The cover agent checks up to eight story names (`story_subjects(limit=8)`, a name that starts with an earlier one's words merged into the shorter), and the first check of a step looks the subjects' P856 websites up once (`official_sites(..., limit=8)`, cached in `temp:cover_official_sites`), so a known site confirms a name match or rules a namesake out. Artwork a company published itself is `official_visual` and allowed; the hard reject applies only to kind `ai_art` (problem `ai_illustration`, AI art made by a third party). Only an exact match turns ai_art into official_visual and drops `ai_illustration` and `publisher_branding`, and when that leaves no problem the verdict becomes `use` (its only fault was looking AI-made; a video still needs its usable frames); a fuzzy match is told to the judge as unverified and overrides nothing. Then, for a rejection, the problems decide the kind: talking_head, unrelated, logo_only or low_quality make it unusable, and document_or_slide, screen_recording_or_webpage or text_heavy make it a text_graphic, so a text card or a presenter cannot pass as a photo or official art. `focus` is the subject box (0-1 fractions); null means the renderer's own crop is fine, so the judge must box group shots, several people and side-by-side portraits even when the box covers nearly the whole frame. `clean` is the largest logo-free box; it may leave out a thin strip of the subject's bottom or one side, never its top. `plan_layout(source_w, source_h, focus, clean)` returns a 4:5 crop, a full-width `band` for a subject wider than any 4:5 crop of the clean area, or None; a clean box that is tiny, cuts more than 15% of the subject or cuts its top is ignored. `apply_focus(media_path, is_video, focus, workdir="", clean=None) -> str` renders that layout. |
| app/tools/reference_photos.py | The free rung before the plain background (imports media_tools, never the reverse). `story_subjects(news, research=None, plan=None, *, limit=4) -> list[str]` names the story's people, organisation and place. `reference_candidates(subjects, *, limit=6, budget_s=40.0, session=None, context="", report=None) -> list[dict]` never raises: Wikidata P18 images of each subject (and an organisation's current leaders, one entry per person), then a Commons search. Only a Wikidata item whose label or alias is the subject counts, and among namesakes the one whose description shares words with `context` (the story) or names a person, body or place. Subjects take turns, at most two photos each, all Wikidata photos before any search result; a search result must be a JPEG whose file name or one category names the subject, and keeps the search's order. Requests time out at (5, 10) s and retry only with 10 s of budget left. Each entry is `{url, origin: 'wikimedia', score, reason, context_url (Commons page), credit ("<Artist> / Wikimedia Commons, <licence>" plus "(cropped, text added)" for an attribution licence, passes the published-text rules), licence, subject, about ('<label>: <Wikidata description>'), role ('subject'/'leader'/'search'), width, height}`; `report['share_alike_skipped']` counts photos left out only for share-alike. Licences are an allowlist (see Cover picture ladder). AI-generated categories and logos are dropped. `official_sites(subjects, *, budget_s=8.0, session=None, context="", limit=4) -> list[str]` returns the P856 websites of up to `limit` subjects; `wikimedia_file(url)` parses an upload URL and `file_credit(url, *, budget_s=10.0, session=None) -> dict | None` returns `{reusable, credit, licence, context_url, title}` for it. Requests send `WIKIMEDIA_USER_AGENT`. Tests use recorded JSON in `tests/fixtures/wikimedia/`, never the network. |
| app/cover_notice.py | `COVER_WARNING`, `PICTURE_WARNING`, `SHARE_ALIKE_NOTE`, `weak_picture_reason(cover) -> str` and `cover_notice_lines(cover: Mapping or None) -> list[str]`: `[COVER_WARNING]` for the drawn background, the picture warning when `weak_picture_reason` names one (text graphic under 3, rejected "as a/an <kind>", unchecked; never `picture_from_reviewer`), plus the share-alike note with either when `share_alike_skipped > 0`; else `[]`. The one wording every review surface and the QA gate use (Telegram review message, console payload and banner, account-free progress line). |
| app/time_window.py | Pure (no I/O). `today_utc()`, `resolve(news, now) -> dict`, `fact_dates(text, anchor)`, `window_problems(facts, window, *, relaxed=False) -> list[str]`, `context_note(window, today) -> str` (no braces), `search_hint(window)`, `describe_range(window)`, `TIME_SECTION` (contains `{time_context?}`) and `with_time_context(instruction)`. See "Time window" below. |
| app/tools/image_gen.py | `generate_slide_image(template_ref: str, copy_lines: list[str], headline: str, slide_no: int, out_path: str, layout_hint="editorial explainer", visual_context="", visual_reference="") -> str` uses `settings.image_model` to generate a text-free exact-aspect lower visual on the first `_GEN_SIZES` rung the API accepts (all exactly 2:1 and divisible by 16; the ladder exists because the API enforces a moving minimum pixel budget that retired the original 1088x544), then merges the complete artwork into the slide's 1080x540 slot (`y=620..1160`) without stretching or cropping. Unexpected source ratios and sourced subject images use aspect-preserving containment rather than cover-cropping. Pillow then adds preferred 76 px headline and 36 px body typography, stepping down only within the readable 60 px/30 px limits when approved copy needs more room. Each copy entry after the headline is a paragraph, separated from the next by half a body line (at least 10 px); the renderer, the copy budget and the pre-render fit gate share this one layout (`brand_layout.fit_inside_copy`), so they always agree. It also applies one coherent top background, a body-only counter beginning at `01`, and the official Baskaran Builds favicon/handle/arrow rail. The cover poster/video and `generate_cta_image(...)` are unnumbered. |
| app/tools/gmail_tools.py | `send_review_email(run_id, bundle: dict, round_no: int) -> dict`, `send_confirmation_email(run_id, ig_permalink) -> dict` - Gmail API (OAuth files from settings), HTML body, inline poster preview + slide thumbnails, Approve/Reject links. |
| app/tools/instagram_tools.py | `publish_carousel(bundle: dict, public_urls: list[str]) -> dict{media_id, permalink}` - Graph API `settings.ig_api_version`: children (VIDEO cover `is_carousel_item`, image children), CAROUSEL container, `media_publish`; enforce <= settings.max_carousel_slides children; poll container status until FINISHED. |
| app/services/artifact_service.py | `SupabaseArtifactService(BaseArtifactService)` - implements the full BaseArtifactService interface (match installed ABC exactly) over S3-compatible Supabase Storage (boto3, settings.s3_*). Keys: `{app_name}/{user_id}/{session_id}/{filename}` + versioning per ABC. Plus `public_url(filename)->str` helper (signed URL) used by publisher/mail. |
| app/services/memory_service.py | `PostgresMemoryService(BaseMemoryService)` (match installed ABC) storing/searching feedback + run summaries in Postgres (asyncpg); simple keyword search is fine. Plus `store_feedback(record: FeedbackRecord)` and `recent_feedback(limit=20) -> list[FeedbackRecord]`. |
| app/services/db.py | asyncpg pool helpers + `db/schema.sql` (news_queue, runs, feedback, pending_reviews tables); `enqueue_news`, `next_queued_news`, `mark_news_done`, `create_run`, `update_run_phase`, `save_pending_review(run_id, session_id, function_call_id)`, `load_pending_review(run_id)`, `record_verdict`. |
| app/tools/research_tools.py | `search_web(query, window=None, *, timeout_s=None, attempts=2) -> dict` returns `{status: 'ok', answer, sources: [url]}` or `{status: 'error', message}` - OpenAI Responses API `web_search` tool on the utility model's bare id; its instructions always carry today's date "(UTC)" and, when there is one, the time window. `save_research_brief(summary, key_facts, suggested_angle, media_candidates, sources, tool_context) -> dict` - validates ResearchBrief, rejects a brief whose fact dates fail `window_problems` for the run's time window (relaxed while `K_RESEARCH_RELAXED` is set, as validate_output is), writes K_RESEARCH either way, and merges media_candidates into news_item.media_urls (cap 8) for the cover agent whenever the relaxed rule passes (an error result then carries `media_added`). |
| app/agents/research.py | `build_research_agent()` - LlmAgent, model settings.planner_model, tools search_web (at most 7 billed searches per attempt, reset by `before_agent_callback`) + save_research_brief; runs FIRST in generate; 2-5 focused searches, verified facts only, each dated (`ResearchFact.date`) and older context marked `background`; sees today's date and the window via `{time_context?}`; planner/phrasing consume the brief via `{research_brief?}` templating. |
| app/agents/planner.py | `build_planner_agent()` - LlmAgent, model settings.planner_model, output_schema CarouselPlan, output_key K_PLAN; instruction covers: points vs prose, slide budget (cover+body+CTA <= 10), <=4 lines/slide, hook per skills/cover-style.md, uses recent feedback memory + the research brief (`{research_brief?}`). The orchestrator writes the saved design's measured text budget to `K_COPY_BUDGET` (`{copy_budget?}`) before the planner step as well as before phrasing, so key points are sized to the room the copy will have. |
| app/agents/first_page_visual.py | `build_first_page_visual_agent()` - LlmAgent + media tools; picks best media_url (or searches source page), builds CoverSpec via compose_cover, saves artifacts, writes K_COVER. Honors K_REWORK_FEEDBACK. Tools: `find_source_clip(search_query='')` (passes the research brief's sources and media_candidates, and the time window when it has a start or an oldest date, to media_tools; C1 result shape plus `calls_left`), `find_reference_photo(subjects=[])` (`{found, subjects, image_candidates, note}` from reference_photos), `download_and_trim` (refuses a URL judged tier 0), `download_image(url) -> {ok, path, error}` (refuses a URL judged tier 0; downloads the URL as found when `find_source_clip` offered its cover-size variant, so the variant is tried first and the original is the fallback), `create_placeholder_background() -> {ok, path, error}`, `retrim_clip(media_path, start_s, length_s)` (keeps the start, ends just before the next scene cut, holds the last frame of a shot shorter than the cover minimum and loops a short source; returns `cut_s`, `held_s`, `looped`), `inspect_cover_media(media_path, is_video)` (at most 6 vision checks per invocation, plus 1 kept for a video downloaded from a held-back `video_url` and 2 per step for reference photos; the result adds `kind`, `tier`, `official` and `best_so_far`), and `build_cover(..., focus_x/y/w/h, use_inspection=True)` (the result adds `drawn_background`, `picture_verdict`, `source_credit`, `source_origin`, `source_media_url`). Every tool keeps to the step's clock (`K_COVER_DEADLINE`, see Cover picture ladder), and `ensure_cover(tool_context, *, budget_s=240.0) -> dict` builds a cover without the model for the orchestrator (`salvaged_from` names the media or 'drawn background'). Verdicts are stored by normalised media path in `cover_media_verdicts` (with `kind`, `tier`, `official` level, `origin`, `url`, `best_start_s`; older records get a derived kind) and clip parentage with start offsets in `cover_media_lineage` (session state, so rework can rebuild the same crop); `cover_media_candidates` (by `_url_key`) and `cover_media_sources` keep each picture's origin, page (for `url` and `video_url` too, from `find_source_clip`'s `provenance`), credit, `about` and `original`; `cover_built_media` and `cover_reviewer_rejected` keep what the last cover showed and what a reviewer turned down. build_cover follows the refusal order in "Cover picture ladder" below, applies the logo-free area to every clip cut from an inspected file, and the subject box to an inspected still whatever its verdict, or to a clip that opens on the judged frame after a `use` verdict. |
| app/agents/phrasing.py | `build_phrasing_agent()` - LlmAgent LiteLlm(settings.phrasing_model), output_schema CopySet, output_key K_COPY; enforces plan style, line budget, plain understandable wording, and the repository-wide prohibition on em dashes. Each slide's `lines[0]` is the headline and every later entry is one paragraph the renderer wraps as its own block (no bullets; its last two headline words take the highlight color). Every body slide pairs a concrete detail with what it means. The text budget comes from the saved design: the orchestrator measures it with the real typesetter (`app/copy_budget.py`) and writes it to `K_COPY_BUDGET` (`{copy_budget?}` in the prompt) just before phrasing runs. `validate_output("phrasing")` then rejects copy that cannot render on the saved design, an entry that stops mid-sentence, a sentence repeated from another slide, and, when the plan's style is `prose`, telegram-shaped slides: two or more body entries under 10 words, or a body made only of such entries (one short caveat after a real paragraph passes), or two or more one-sentence entries under 15 words with no linking word (skipped on a text box too small for a paragraph, where the budget asks for one short sentence). The one repair retry fixes these before any slide image is paid for. The prose shape is advice, so it no longer blocks the retry itself, and it is not checked at all when the step re-runs on a human reviewer's rework feedback (`validate_output(..., under_rework=True)`): its repair note would reach phrasing as the reviewer's own words and undo a request for shorter copy. Re-checking a saved checkpoint on resume keeps only the fit check. Curly quotes are straightened in `CopySet` (slide lines and caption), in the plan's `hook_title`/`hook_highlight` and `CoverSpec.title`/`highlight` (the cover text), and by `generate_cta_image` for the CTA headline and lines, which never pass through a schema. Audience-facing Pydantic schemas reject forbidden characters, broken encoding, placeholders, and conservative nonsense patterns before rendering or publishing. |
| app/agents/template_design.py | `build_template_design_agent()` - LlmAgent + image_gen tool; passes the news item, research brief, and slide purpose into each visual prompt. Only subject-introduction slides receive an aspect-preserved sourced cover-media image for deterministic lower-zone compositing. Plan purposes read `Reader asks: ...? Payoff: .... Visual: ...`; only the Visual part (never the reader's question) decides the subject image, and an archetype named there wins over word cues in the copy. The Visual part ends at its first clause break, so a note after it ("Visual: technical proof, identify the source") never counts; "technical" outranks "evidence", and "the real subject" (or a person, product or photo) is the editorial explainer. In the copy cues, a day next to a month name is a date, not a statistic. Saves one PNG per body slide and writes K_BODY_SLIDES. |
| app/agents/cta.py | `build_cta_agent()` - picks CTA type (plan.cta_hint + content), renders CTA slide via image_gen, writes K_CTA_SLIDE with link_url from settings. |
| app/agents/stitch_verify.py | `build_stitch_verify_agent()` - assembles Bundle (ordered artifacts: cover video first), QA checks (count <= 10, durations 4-15 s, copy-vs-rendered text via LLM vision or size heuristics, line budgets, fixed body-slide-number geometry, exact body/CTA footer furniture and safe-area padding, the cover-picture gate in "Cover picture ladder" below), appends a Wikimedia photo credit to the Bundle caption, writes K_BUNDLE + K_QA_REPORT; critical issues → auto-route back (set K_REWORK_PLAN) without mailing. |
| app/agents/review_dispatcher.py | `build_review_dispatcher_agent()` - sends review mail (gmail_tools), then `await_human_review` LongRunningFunctionTool; on resume writes K_VERDICT from the tool response; persists pending call id via db.save_pending_review. |
| app/agents/feedback_router.py | `build_feedback_router_agent()` - LlmAgent (utility model), output_schema ReworkPlan, output_key K_REWORK_PLAN; maps feedback text → targets from REWORKABLE_AGENTS only. |
| app/agents/publisher.py | `build_publisher_agent()` - public URLs from artifact service, publish_carousel, confirmation mail, writes result to state + runs table. |
| app/agents/learner.py | `build_learner_agent()` - stores FeedbackRecord (memory_service), and when a rule repeats (>=2 similar feedbacks) appends a distilled rule to the relevant skills/agents/<name>.md or skills/design-skill.md under "Learned rules". |
| app/orchestrator.py | `CarouselOrchestrator(BaseAgent)` per the state machine above; children passed via sub_agents so adk web draws the graph; emits one concise Event per phase transition for realtime visibility. Init stamps `K_TIME_WINDOW`; preparing research, planner and phrasing writes `K_TIME_CONTEXT`; research gets `K_RESEARCH_RELAXED` (true under rework and on its retry); each cover attempt gets `K_COVER_DEADLINE` (now + 900 s; a step times out at 1200 s with `StepTimeoutError`), and a cover step that times out, crashes or ends twice without a valid cover is salvaged by `_salvage_cover` (`first_page_visual.ensure_cover`, 240 s) before the step may fail; the research progress text shows the window; the account-free review progress line appends `cover_notice_lines(bundle.cover)`. |
| app/agent.py | builds everything, exposes `root_agent` (module-level) for `adk web`/`adk run`; also `build_runner()` returning a Runner wired to DatabaseSessionService(settings.database_url), SupabaseArtifactService, PostgresMemoryService (with in-memory fallbacks when env is missing so `adk web` works locally). |
| fetcher/fetch_news.py | pulls Gmail newsletters (query settings.newsletter_query), RSS (feedparser), YouTube channel feeds; dedupe by URL hash into news_queue; `python -m fetcher.fetch_news --run-one` pops one item and starts a pipeline run via build_runner(). |
| README.md | setup (venv, ffmpeg, .env), Supabase schema apply, `adk web` for the REALTIME AGENT GRAPH + event/state inspector (this is the "agent graph, loop visuals, realtime operation" surface), running fetcher/review API, the rework loop explained, NO git commits until review. |

## Package init files

`app/__init__.py`, `app/agents/__init__.py`, `app/tools/__init__.py`,
`app/services/__init__.py` exist and stay EMPTY (docstring only) - no
re-exports, to keep imports acyclic. Import submodules directly
(`from app.tools import media_tools`).
