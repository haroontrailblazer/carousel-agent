# Carousel pipeline audit

Checked on 2026-09-10. Changes are local source changes and have not been deployed.

## Expected flow

Topic, pasted article URL or newsroom item -> research brief -> editorial plan -> sourced cover image/video -> cover PNG and MP4 -> phrased body copy -> body PNGs -> independently designed CTA -> artifact and layout QA -> review/download -> explicitly approved Instagram publishing -> done.

The run snapshots its design, branding, links, maximum slide count and Instagram account. Changes to the design library affect new runs. OpenAI credentials and models come only from saved workspace AI settings.

## Findings and fixes

| Boundary | Defect or failure | Behavior after this audit |
| --- | --- | --- |
| Research through CTA | Agents could return without saving their output and the next agent would still run. | Validate every hand-off. Empty research, missing cover files, missing/duplicate slide indexes, missing caption and missing CTA are stopped. One output-repair attempt is allowed; repeated failure identifies the unfinished agent. |
| Resume | A stopped generation phase repeated every agent, including successful image work. | Completed agents are checkpointed in session events. Resume verifies their outputs and starts at the unfinished step. |
| Rework | Interrupted rework could consume another review round and regenerate completed pieces. | Persist active targets, round and completed steps. Resume finishes the same round. |
| Copy changes | Rephrasing refreshed body slides but left CTA dependent copy stale. | Rephrase -> body renderer and CTA; frontend rework predictions match this dependency. |
| Stalls | Text requests and agent tool loops could consume most of the run deadline. | Text requests have a 180-second timeout and one provider retry. Each agent invocation has a 20-minute/160-event limit. Existing run deadlines and cancellation remain in place. |
| Cover media | Source videos or images can fail to download or process. | Existing bounded source search, source-image fallback, still-image-to-MP4 fallback and FFmpeg timeouts remain. Both PNG and MP4 must be saved before continuing. Real rendering tests cover both input types. |
| Design editor | No separate CTA canvas; rendering reused the inside layout. | CTA is a third editable canvas, with independent text/image/logo/handle placement and style. Saved legacy designs inherit the inside layout once. No image avoids an unnecessary image generation call. |
| Mobile editor | Three slide tabs could widen the grid beyond the viewport. | Explicit bounded grid track and flexible tabs fit 390px. Control labels were made unambiguous. |
| QA | An old successful QA result could survive a failed verification invocation. | Clear the previous result before verification. No new QA report means pause, not review. |
| Storage outage | Artifact listing failure skipped existence checks, allowing review without proving files existed. | Pause QA with a storage message. Resume retries verification without regenerating the carousel. |
| First/last PNG | Cover poster and CTA were checked for existence but not valid image dimensions. | Apply the PNG validation used for body slides, routing corrupt first/last PNGs to their owners. |
| Review UI/API | Any connected account unlocked UI actions, including runs with a different or absent publish target. The API had no matching account gate. | Require the run's own usable account. Download-only runs never approve, reject, publish or automatically deliver files. The server also requires review phase, passed QA and an assembled bundle. |
| Concurrent review | A losing reviewer could overwrite the cover selection before the verdict claim. | Cover choice travels in the accepted verdict through resume and is committed before publishing. The losing review cannot alter the bundle. |
| Lost publish response | A later invocation could ignore a previous non-retryable result and post again. | Reserve a durable publish receipt with atomic compare-and-swap before external publishing. Publishing/uncertain receipts block retries even when session event state was lost. |
| Post succeeded, follow-up failed | Permalink or confirmation failure could erase successful publishing state. | Preserve the media ID before notification. Permalink lookup is best-effort; confirmation failure does not cause another post. |
| Queue recovery | Interrupted runs could release their story to the queue while remaining resumable. | Keep interrupted runs' news claims reserved to avoid duplicate generation. |
| Article ingestion | Malformed HTML without a closing title tag could crash an otherwise readable article. | Fall back to the extracted article text for the title. Direct ADK topic input also retains its first source URL. |

## Recovery choices

- **Missing or malformed output:** one automatic repair, then use Resume/Retry. Completed upstream agents stay saved.
- **Failed QA:** target the responsible agents within the existing automatic QA budget. A human rejection uses its separate review budget.
- **Storage/model outage:** restore the connection or settings and resume. Paid work from an interrupted, uncheckpointed agent can still need repeating.
- **Expired Instagram account:** reconnect the same account in Profile. A different account never silently receives the post.
- **No Instagram account on the run:** preview and download. Select an account when creating a new carousel if app publishing is wanted.
- **Notification failure:** review in the app. The carousel and a known successful post remain valid.
- **Uncertain publish or process loss during publish:** automatic sending is blocked. An operator must reconcile the run's `publish_receipt:<run_id>` record with the selected Instagram account. Do not clear a receipt or retry by creating another run until the result is known. Exactly-once delivery across an external API and a database cannot be guaranteed; this implementation stops rather than risking a duplicate.

## Verification and limits

Final checks: **598 backend tests passed, 1 skipped, 34 subtests passed** (13 warnings); the frontend TypeScript/production build passed. Desktop/mobile browser checks passed with no page errors. The skipped backend test is reported by the existing suite; both added FFmpeg smoke tests ran and passed.

- Deterministic pipeline tests exercise research -> cover -> copy -> slides -> CTA -> QA -> pause, rejection/rework, approval and completion. Agent/model outputs and external posting are simulated; state transitions, hand-off checks and ordering are real application code.
- Additional regressions cover missing output, resume checkpoints, interrupted rework, stale QA, storage outage, corrupt cover/CTA files, wrong-account review, concurrent cover choices, uncertain publish receipts and failed notification/permalink lookup.
- Real local FFmpeg tests render synthetic image and video sources into silent 1080x1350 H.264 MP4s plus PNG posters, verifying duration, full-bleed media and the black title base.
- Browser checks exercise the real CTA editor and review components with mocked API responses: independent save/reload, a 390px viewport without horizontal overflow, and connected/disconnected/wrong-account/unverified review states.
- Read-only live configuration check: Supabase is reachable and encryption is configured. **No workspace OpenAI key is saved yet.** Add it in Profile > AI & models and select from the discovered models before running generation. Environment OpenAI keys are intentionally ignored and were not edited.
- No billed model/image generation or real Instagram publishing was performed. Source ranking, factual quality, third-party permissions, content availability, and deployed production behavior still need a live carousel review after configuration and deployment. A video source can be unavailable; the existing fallback is a sourced still image animated to MP4, with a placeholder as last resort.
- The existing run scheduler assumes one application instance for generation. Publish receipts protect against duplicate sends across workers, but horizontal generation workers still need distributed leases before scaling beyond that assumption.
