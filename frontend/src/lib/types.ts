/**
 * Shapes returned by the console API.
 *
 * These mirror web_api/routes_runs.py. They are hand-written rather than
 * generated because the surface is small and stable; the drift risk that
 * actually matters - agent and phase NAMES - is closed differently, by
 * fetching them from /api/meta and asserting against the local list (see
 * pipeline.ts). A renamed constant would otherwise produce silently blank rows
 * in the trace with no error anywhere.
 */

export type Phase = "generate" | "qa" | "review" | "rework" | "publish" | "done"

export type RunStatus =
  | "running"
  | "awaiting_review"
  | "done"
  | "interrupted"
  | "failed"
  | "cancelled"

export type EventKind =
  | "phase"
  | "progress"
  | "tool"
  | "error"
  | "terminal"
  | "gap"

export type RunSummary = {
  run_id: string
  news_id: string | null
  phase: Phase
  status: RunStatus
  review_round: number
  title: string | null
  source: string | null
  requested_by: string | null
  created_at: string | null
  updated_at: string | null
  is_live: boolean
}

export type QAIssue = {
  severity: string
  slide_index: number | null
  message: string
}

export type RunDetail = RunSummary & {
  news: {
    title: string
    summary: string
    source_name: string
    source_url: string
  }
  phase_state: Phase
  rework_round: number
  caption: string
  slide_count: number
  qa: { passed: boolean | null; issues: QAIssue[] }
  verdict: { status: string; feedback: string; reviewer?: string } | null
  publish: { media_id: string | null; permalink: string | null; error: string | null }
  token_usage: Record<string, number>
  last_seq: number
  /**
   * Whether a human decision is still wanted.
   *
   * NOT monotonic. A failed background resume restores the pending row, so a
   * run can go pending -> not pending -> pending again. Anything that reads
   * this once and caches the answer will eventually show the wrong thing.
   */
  pending_review: boolean
}

export type SignedArtifact = {
  filename: string | null
  url: string | null
  error?: string
}

export type RunArtifacts = {
  run_id: string
  expires_in: number
  caption: string
  cover: {
    poster: SignedArtifact | null
    video: SignedArtifact | null
    /** No source clip was found, so the "cover" is a still - do not render a
     *  <video> element for it. */
    is_still: boolean
    duration_s: number
  }
  slides: (SignedArtifact & { index: number })[]
  cta: SignedArtifact & { cta_type: string | null; link_url: string }
  ordered: string[]
}

export type RunEvent = {
  seq: number
  kind: EventKind
  author: string
  text: string
  data: Record<string, unknown>
  created_at: string | null
}

export type QueueItem = {
  id: string
  title: string
  summary: string
  source_name: string
  source_url: string
  created_at: string | null
}

export type Meta = {
  agents: string[]
  reworkable_agents: string[]
  phases: Phase[]
  statuses: RunStatus[]
  reject_question: string
  max_slides: number
  /** False when IG_USER_ID / IG_ACCESS_TOKEN are unset: approving will still
   *  record the verdict, but publishing will fail loudly. */
  publish_configured: boolean
}

export type Identity = {
  email: string
  role: string
  is_admin: boolean
  source: string
}
