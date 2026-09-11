## Carousel Factory

Carousel Factory turns AI/product news (a new model release, a Lovable or
Supabase feature drop, a paper worth explaining) into finished Instagram
carousels - planned, written, designed, QA-checked, and ready to download
with optional approved Instagram publishing - using a Google ADK multi-agent pipeline. A fetcher pulls
updates from RSS feeds and YouTube channels into a queue;
each queued item drives one pipeline run: an Editorial Planner decides
structure (points vs prose, slide count, hook title), a First-Page Visual
agent builds the cover as a **sourced** 4–8 s video (never AI-generated -
trimmed from the announcement itself, composited with the brand overlay per
`skills/cover-style.md`), a Phrasing agent writes the copy, a Template Design
agent renders body slides with gpt-image-2 against the designer's templates, a
CTA agent renders the closing slide, and a Stitch & Verify agent assembles and
QA-checks the bundle.

Every QA-passed run stops at **Needs review**. Without an Instagram account
selected for that run, preview and download are available; no approval or
rejection is accepted and no files are automatically sent to Telegram.
For Instagram runs, approval, rejection and chat feedback require that exact
account to remain connected. Both the UI and server enforce this, together
with passed QA. Connecting another account does not change a run's identity.
Telegram can notify reviewers and send publication confirmations; a failed
notification does not prevent a ready carousel from being reviewed in the app.

On the Review page, choose the image or video cover, then click **Download
carousel** to save a ZIP containing that cover, all body slides, and the CTA
in carousel order. Downloading does not approve or publish the run.

To publish, connect one or more Professional Instagram accounts from **Profile →
Instagram**, using a separate access token for each account. Tokens are encrypted
at rest. Choose the account before starting: its identity is used for the artwork
and publishing. New connections use token entry only, with no Instagram OAuth
redirect. A connected-account run sends a Telegram review request and pauses.
**Approve and publish** publishes to that selected account and sends a confirmation.
**Reject** (feedback compulsory) reworks the requested parts and asks for review
again. Every piece of feedback is stored in
long-term memory, and recurring feedback is distilled by the Learner agent
into account-owned learned rules in the database. The shared `skills/` files
remain the base instructions; one person's feedback never changes another
person's agent instructions.
State and the run ledger live in Supabase Postgres; media artifacts live in
Supabase Storage. The architecture is modeled in `architecture/carousel.c4`
(LikeC4 - views: `index`, `containers`, `agentPipeline`, `happyPath`,
`rejectRework`, `approvePublish`, `production`).

> **IMPORTANT - no git commits.** Nothing in this working tree gets committed
> or pushed by tooling or agents. The tree stays as-is, awaiting the owner's
> review. Commit only when the owner has reviewed and says so.

---

## Repo map

| path | what it is |
|---|---|
| `app/agent.py` | `root_agent` (discovered by `adk web` / `adk run`) + `build_runner()` used by the fetcher and review API |
| `app/orchestrator.py` | `CarouselOrchestrator` - the re-entrant phase state machine (`generate → qa → review → publish/rework → done`) |
| `app/agents/` | one file per agent (planner, first_page_visual, phrasing, template_design, cta, stitch_verify, review_dispatcher, feedback_router, publisher, learner) |
| `app/tools/` | media (yt-dlp + FFmpeg), gpt-image-2, Gmail, Instagram Graph API tools |
| `app/services/` | Supabase Storage artifact service, HTTPS session and memory services, fixed database RPCs |
| `fetcher/fetch_news.py` | pulls newsletters/RSS/YouTube into the news queue; starts runs |
| `db/schema.sql` | `news_queue`, `runs`, `feedback`, `pending_reviews` |
| `skills/` | the editable harness: cover style, design skill, per-agent instructions |
| `docs/CONTRACTS.md` | the binding code-level spec |
| `architecture/carousel.c4` | C4 model (preview: `npx likec4 start architecture`) |

---

## Setup

Prerequisites: Python 3.11+ (developed on 3.13), FFmpeg, a Supabase project,
an OpenAI API key saved through Profile > AI & models, and optional feed
integrations. Telegram notifications
need a bot connected through Profile. Instagram publishing optionally
uses a Professional account token with content-publishing permissions.

### 1. Python environment

```powershell
cd C:\Projects\carousel
python -m venv .venv
.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. FFmpeg

FFmpeg is a system dependency, not a pip package. Install it and make sure
`ffmpeg` is on `PATH` (Windows: `winget install Gyan.FFmpeg` or grab a build
from https://ffmpeg.org; macOS: `brew install ffmpeg`). If it lives elsewhere,
point `FFMPEG_BIN` in `.env` at the executable.

### 3. Configuration

```powershell
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

Use `.env` for server infrastructure, Supabase and encryption configuration.
Personal RSS and YouTube sources live in **Profile > Connections > Your newsroom sources**.
Save the OpenAI key and model choices in **Profile > AI & models**. Model
dropdowns load from that key. Existing OpenAI credentials in `.env` are kept
for copying but are never a runtime fallback. Never commit `.env`.

Instagram credentials are connected per account in Profile, not through a
global account token. Download-only artwork uses the selected design branding.

Set Substack and YouTube destinations in **Designs > Your branding >
Call-to-action links**. Each design saves its own optional URLs; new tasks
snapshot those links with the selected design. These links are not environment
variables. With no links configured, use a follow or comment CTA.

Use **Designs > Carousel length > Maximum slides** to save a 3-10 slide
limit for each design. This total includes the cover and final CTA. New tasks
keep a snapshot of this setting; planning, QA and publishing enforce it.
Older designs without this setting default to 10 slides.

### 4. Database (Supabase Postgres)

The application uses `SUPABASE_URL` and a server-only Supabase key for all
queries, agent sessions and memory. It does not need a database connection URL.
For a new project, apply `db/migrations/005_transfer_baseline.sql` and migrations
`006` through `011` in order using Supabase Dashboard → SQL Editor. For `009`,
configure the existing private bucket as described in `db/access-policies.md`.
Schema changes run as an administrator during setup, never at app startup.

The HTTPS RPCs grant execution only to `service_role`. Browser Auth continues
using `SUPABASE_ANON_KEY`; browser roles cannot read application tables or call
backend RPCs. See [HTTPS database setup](db/https-database.md).

### 5. Artifact bucket (Supabase Storage)

Create a **private** bucket named `corousel-media` (the fixed application bucket)
in Supabase Dashboard → Storage. Native Storage uses
`SUPABASE_URL` and one **server-only** key: `SUPABASE_SECRET_KEY` (preferred)
or `SUPABASE_SERVICE_ROLE_KEY` (legacy JWT). No separate S3 setup is needed.
The browser uses `SUPABASE_ANON_KEY` for Auth; background uploads cannot use
an anonymous identity. The same server key authorizes database RPCs.
Never put a server key or management personal access token in `VITE_*` variables.

Existing `SUPABASE_S3_*` deployments continue working until a native key is
configured. The bucket name is fixed in `app/config.py`; `MEDIA_BUCKET` environment
values are ignored.
Media stays private; external publishers receive expiring signed download URLs.
See [database and egress notes](db/egress.md) for validation and rollout steps.

### 6. Private accounts and migration

People can choose **Create your account**, confirm their email, then sign in.
Configure [custom SMTP in Supabase Auth](https://supabase.com/docs/guides/auth/auth-smtp)
before opening signup to the public. Supabase's default email service only
sends to project team addresses; confirmation and password recovery need
a production email provider. Keep email confirmation enabled.

Each account owns its designs, newsroom, runs, chat/session history, learned
feedback, integration keys and settings. New accounts start without another
person's data or credentials; shared starter design templates and public RSS
suggestions remain available.

Migration `012` assigns the existing shared workspace to
**haroon@closefuture.io** and preserves confirmed users' existing personal
design libraries. Media is stored in the private `corousel-media` bucket under
`users/<Supabase user UUID>/`. The backend derives that prefix from the verified
session; callers cannot supply a different owner's path. Browsers cannot list
the bucket or access database tables directly. Preview/publishing URLs expire.

For an existing deployment, run `scripts/migrate_private_workspaces.py` with
`--token-file <temporary-management-token-file>` to prepare and verify media
copies. After active runs finish, run it with `--apply` and deploy the matching
application immediately. It preserves original objects, checks row counts in
the migration transaction, and updates Auth email redirects to the production
URL. Metadata and copy manifests remain under ignored `.work/`; remove the
temporary token file afterwards. Existing sessions must sign in again.

Database RPCs enforce tenant policies using a restricted worker role. Only the
server can dispatch a request for a verified workspace. Keep server credentials,
`SECRETS_KEY` and `SESSION_SECRET` in the deployment environment; those are
infrastructure secrets, not personal integration settings.

This supports separate accounts on one application instance. Agent task queues
and live event subscriptions remain process-local: use one application worker
until a distributed job worker/event bus is introduced for horizontal scaling.

---

## Running

### `adk web` - the realtime surface

This is the window into the system: the live agent graph, the event stream,
and the session-state inspector. From the **repo root**, with the venv active:

```powershell
adk web
# equivalent: python -m google.adk.cli web
# custom port: adk web --port 8000
```

Verified against the installed google-adk 2.7.0 CLI: `adk web [AGENTS_DIR]`
defaults `AGENTS_DIR` to the current directory and scans its subfolders for
agent packages, so run it from `C:\Projects\carousel` and it discovers the
`app/` package (via `app/agent.py`'s module-level `root_agent`). Open
http://127.0.0.1:8000 and pick **app** in the app dropdown.

What you get, live, while a run executes:

- **Agent graph** - `carousel_orchestrator` at the root with all eleven
  sub-agents attached (they are declared as `sub_agents`, which is what makes
  ADK render the tree): research, planner, first_page_visual, phrasing,
  template_design, cta, stitch_verify, review_dispatcher, feedback_router,
  publisher, learner. The research agent runs FIRST: it web-searches the
  update (OpenAI Responses `web_search`), saves a source-cited fact brief to
  state for the planner/phrasing agents, and feeds any official announcement
  media it finds to the cover agent.
- **Event stream** - every LLM turn, tool call and tool response as it
  happens. The orchestrator additionally emits one concise event per phase
  transition, authored by `carousel_orchestrator`, of the form
  `[phase] generate -> qa`, `[phase] qa -> review`, `[phase] rework -> qa` …
  - these are the state machine's heartbeat and make the **rework loop
  directly visible**: after a rejection you'll see
  `[phase] review -> rework`, the feedback_router's `ReworkPlan`, only the
  blamed agent(s) re-running, then `[phase] rework -> qa` and
  `[phase] qa -> review` as the corrected carousel goes back out for review.
- **State inspector** - the session state is the pipeline's entire memory
  (`phase`, `run_id`, `carousel_plan`, `cover`, `copy_set`, `body_slides`,
  `cta_slide`, `bundle`, `qa_report`, `review_verdict`, `rework_plan`,
  `rework_round`, …; keys defined in `app/state.py`). Because the review
  pause ends the invocation and the resume is a NEW invocation, everything
  lives here - inspecting it tells you exactly where a run stands.

To exercise the pipeline from the UI, send a message like *"run"* - the
orchestrator reads its phase from state and proceeds. Runs started by the
fetcher normally seed `news_item` in state first; without one the run halts
early with an explanatory event.

The application console and `app.agent.build_runner()` share persistent
Supabase HTTPS sessions. Standalone `adk web` creates its own development
session service, so its sessions are separate from production review runs.
Use the application's console for the complete generate → review → resume flow.

`adk web` is a development server with **no authentication** - keep it on
localhost. (Terminal alternative without the UI: `adk run app`.)

### Fetching news

```powershell
python -m fetcher.fetch_news --owner <Supabase-user-UUID> --fetch     # pull newsletters + RSS + YouTube, dedupe into news_queue
python -m fetcher.fetch_news --owner <Supabase-user-UUID> --run-one   # pop the next queued item and start one pipeline run
```

`--fetch` reads the selected account's RSS and YouTube sources from the database, dedupes by URL hash, and enqueues. `--run-one`
starts a run via `build_runner()` - the run executes generate → qa → review
and then **pauses**, waiting for the email verdict. In production these are a
Cloud Scheduler → Cloud Run job; locally you run them by hand.

### Reviewing a carousel

Reviews happen in the console, at `/tasks/{run_id}?tab=review`. Telegram sends
one **REVIEW CAROUSEL** button pointing there, so the reviewer sees the cover
video and still side by side, every slide at full size, and the caption
counted against Instagram's limit before deciding.

Signing in is required. There used to be standalone Approve/Reject pages that
needed no credentials, on the reasoning that a Telegram link opens where
nobody can log in - but approving auto-publishes to Instagram, so any leaked
URL was a permanent, anonymous publish button. Those pages are gone; the
verdict now goes through `POST /api/runs/{id}/verdict`, which records who
decided.

Set `PUBLIC_BASE_URL` in `.env` to the address the button should carry.
Telegram refuses a non-public URL in a button, so with `localhost` (or nothing)
the review message falls back to a plain-text link - visible and copyable, just
not tappable. Tunnel the console or deploy it to get a real button.

---

## Token & cost traceability

Two independent layers:

- **Run totals (always on, no setup).** The orchestrator sums every model
  call's `usage_metadata` (Gemini natively; OpenAI via the LiteLLM wrapper,
  which maps usage the same way) plus gpt-image-2 token usage from the
  Images API into session state under `token_usage` -
  `prompt_tokens / output_tokens / total_tokens / llm_calls` and
  `image_*` counters. The `[done]` event prints the full line, e.g.
  `tokens in 41,203 / out 9,882 / total 51,085 over 14 LLM call(s) +
  31,440 image tokens over 8 image call(s)`, and every image call is also
  logged individually as `[tokens] gpt-image-2 images.edit: ...`.
- **Langfuse (optional external analytics).** An administrator connects a
  Langfuse project under **Profile & settings > Tracing** using its HTTPS
  server URL, public key and secret key, then enables export. Both keys are
  encrypted in workspace storage and never returned by the settings API.
  Langfuse values in `.env` are ignored. Changes apply to new and resumed
  runs; each invocation keeps its own destination until it ends.
  `app/langfuse_tracing.py` routes OpenInference ADK spans and image token
  usage through an isolated OTLP exporter. It exports timings and usage,
  with prompt/response contents hidden. Disabled or unavailable Langfuse
  never blocks creation or the console's local trace and token totals.

## The review flow

1. After QA passes, the Review Dispatcher mails the preview (cover poster +
   slide thumbnails) with **Approve** / **Reject** links, then calls the
   `await_human_review` long-running tool - the ADK invocation ends, the
   pending function-call id is persisted in `pending_reviews`, and the run
   sleeps. It survives restarts: everything needed lives in the session DB.
2. The links open a **confirm page** first (so a mail scanner prefetching the
   GET cannot auto-approve anything).
   - **Approve** - feedback optional. Add a note if you want the Learner to
     remember something ("great, but hooks could be shorter").
   - **Reject** - feedback **compulsory**. The form asks what exactly is not
     good (first visual / texts / design / CTA / other). Be concrete: the
     text is what routing and learning run on.
3. On submit the review API builds a function response for the pending call
   and resumes the same session through `Runner.run_async` - a new invocation
   that picks the state machine up at the `review` phase.
4. On **reject**: the Learner stores the feedback, then the Feedback Router
   maps it to targets - only from {planner, first_page_visual, phrasing,
   template_design, cta} - and **only those agents re-run**, with your
   feedback injected as their highest-priority instruction. "The first visual
   is not good" → only `first_page_visual` re-runs; everything else is kept.
   If the router blames the `planner`, dependents whose inputs changed re-run
   too. Then stitch/QA re-assembles and a fresh review mail goes out.
   Rounds are capped by `MAX_REWORK_ROUNDS` (default 5).
5. On **approve**: the Learner stores any optional feedback, then publishing
   proceeds.

## Publishing

The Publisher agent turns the approved bundle into an Instagram carousel via
the Graph API (`IG_API_VERSION`): it gets signed public URLs for every
artifact from the Supabase artifact service, creates the children (the cover
as a plain VIDEO carousel item - not a Reel - then the image slides; max 10
children; the cover's 4:5 aspect ratio governs the whole carousel), creates
the CAROUSEL container, polls it until `FINISHED`, publishes, writes the
result to the run ledger, and sends you a confirmation mail with the
permalink.

## `skills/` - the editable harness

`skills/` is the system's personality, and it is meant to be edited:

- `skills/cover-style.md` - the cover composition contract (media zone, black
  grain dissolve, solid `#8FB832` highlight rules).
- `skills/design-skill.md` - the Baskaran Builds body/CTA slide system,
  including layout archetypes, the single `#8FB832` accent, exact safe areas,
  and the official website footer favicon.
- `skills/references/` - visual proofs and canonical brand assets. Footer
  furniture is composited deterministically after image generation so the
  logo, handle, arrow, and padding remain exact on every slide.
- `skills/agents/<name>.md` - one instruction file per agent, loaded fresh
  from disk at agent-build time.

The Learner agent appends distilled rules under "Learned rules" in these
files when the same feedback recurs (≥ 2 similar complaints) - this is the
mechanism by which review feedback becomes a **permanent** upgrade of the
harness rather than a one-off fix. Edit the files yourself any time; the next
run picks the changes up. Treat diffs in `skills/` as reviewable output of
the system.

---

## Honest caveats

- **Instagram prerequisites are real.** Content publishing needs an Instagram
  professional (business/creator) account linked through a Facebook app with
  approved publishing permissions and a long-lived access token, and the
  Graph API only ingests media from **publicly reachable URLs** - so
  publishing requires the Supabase artifact bucket (signed URLs); the
  in-memory artifact fallback can never publish. Carousels are capped at 10
  items and the video cover must satisfy Meta's format rules.
- **yt-dlp is fragile by nature.** Sites change, formats break, and downloads
  from cloud/datacenter IPs get throttled or blocked (YouTube especially).
  The cover build is best-effort: when no usable 4–8 s clip can be fetched it
  falls back to the update's own image as a static cover. Also remember
  sourced clips are third-party media - the rights check is on you.
- **gpt-image-2 renders text imperfectly.** Body slides are *generated
  images*; even with strict verbatim-text prompts the model can mangle words.
  Stitch & Verify QA-checks rendered text against the approved copy and
  routes failures back, but that costs regeneration rounds (real API money)
  and is not infallible - the review mail is the final gate for typos.
- **The review pause depends on shared state.** Resume only works when the
  fetcher, review API (and `adk web`, if you drive runs from it) share the
  same session database and app name - see the `--session_service_uri` /
  `APP_NAME` note above. With in-memory fallbacks a paused run dies with its
  process.
- **`adk web` is unauthenticated** and for local development only; on Windows
  the CLI may disable auto-reload (a known ADK limitation - restart it after
  code changes, or use `--reload_agents` for agent files).
- **AI settings live in Profile & settings → AI & models.** Administrators
  can save a workspace OpenAI API key and choose the Planner, Utility,
  Phrasing and Image models. The key is encrypted in `app_config` using
  `SECRETS_KEY` and is never returned to the browser. A blank key field keeps
  the existing key. Saving does not modify `.env`. Legacy OpenAI keys and model variables in
  `.env` are ignored. Models use the saved choices or the built-in suggested
  defaults; a saved API key is required before a run can start.
  New and resumed production runs refresh these settings from Supabase and
  keep a private snapshot for that invocation, including image generation
  and web search. Active runs keep their current settings. Text model IDs
  use the `openai/` prefix; image model IDs are bare. GPT-5 text agents keep
  `reasoning_effort="high"`. Suggested Planner and Phrasing models are
  `openai/gpt-5.6-sol`, Utility is `openai/gpt-5.4-mini`, and Image is
  `gpt-image-2`. Each role has a dropdown populated from OpenAI's model list
  using the saved key. A replacement key can load its models before saving;
  previewing does not persist the key. Saving rechecks key-visible models
  and rejects selections that are no longer available. The list separates
  general text models from GPT Image models and does not guarantee support
  for every tool used by a carousel run. Discovery generates no content.
  Standalone ADK model calls also refuse missing workspace credentials
  instead of falling back to environment keys.


### Pipeline recovery and CTA design

The design editor has **Cover**, **Inside slide**, and **CTA** canvases. Each
has independent geometry, typography, colors and image settings; old designs
start the CTA from their inside-slide layout. The CTA renderer uses that saved
layout, including No image, logo placement and handle placement.

Generation validates every hand-off and checkpoints completed agents. A
missing or inconsistent output gets one automatic repair attempt. Resume
continues from the unfinished agent; interrupted rework retains its round and
completed steps. QA waits on unavailable storage instead of redrawing slides.
Publish attempts have durable receipts that block duplicates after a restart.
An uncertain Instagram response requires checking the account before another
publish attempt; notification and permalink failures do not erase a known post.

See [the end-to-end audit](docs/pipeline-audit.md) for the failure matrix,
verification coverage, and remaining setup requirements.
