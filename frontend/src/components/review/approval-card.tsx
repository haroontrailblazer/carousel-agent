import * as React from "react"
import { CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Chip, MutedChip } from "@/components/ui/chip"
import { Textarea } from "@/components/ui/input"
import { REJECT_CATEGORIES, AGENT_LABELS, predictRework } from "@/lib/pipeline"
import type { RunDetail } from "@/lib/types"

/**
 * The decision surface.
 *
 * Deliberately asymmetric: Approve is the lime pill, Reject is a quiet ghost
 * button. Approve is the action that publishes publicly, so it is behind a
 * confirmation step - the email flow already refuses to decide anything on a
 * GET, and the console should not be laxer than the email.
 *
 * The same run can be decided from Telegram, so this component has to handle
 * THREE states, not two:
 *
 *   1. pending          - a decision is wanted; show the buttons.
 *   2. decided          - a verdict is recorded; show what it was.
 *   3. being processed  - no pending row and no verdict yet, because a resume
 *                         is in flight. Showing "already decided" here would
 *                         be wrong, and showing the buttons would be worse.
 *
 * And it is not monotonic: a failed resume restores the pending row, so a run
 * can go pending -> not pending -> pending again.
 */
export function ApprovalCard({
  run,
  publishConfigured,
  onApprove,
  onReject,
  busy,
}: {
  run: RunDetail
  publishConfigured: boolean
  onApprove: () => void
  onReject: (feedback: string) => void
  busy: boolean
}) {
  const [rejecting, setRejecting] = React.useState(false)
  const [feedback, setFeedback] = React.useState("")
  const [picked, setPicked] = React.useState<string[]>([])
  const [confirming, setConfirming] = React.useState(false)

  const predicted = React.useMemo(() => {
    const targets = REJECT_CATEGORIES.filter((c) => picked.includes(c.key)).flatMap(
      (c) => [...c.targets],
    )
    return predictRework(targets)
  }, [picked])

  // --- state 2: already decided ------------------------------------------
  if (!run.pending_review && run.verdict) {
    const approved = run.verdict.status === "approved"
    return (
      <Card className="p-5">
        <div className="flex items-start gap-3">
          {approved ? (
            <CheckCircle2 className="mt-0.5 size-5" style={{ color: "var(--phase-done)" }} />
          ) : (
            <XCircle className="mt-0.5 size-5 text-[var(--destructive)]" />
          )}
          <div className="min-w-0 flex-1">
            <p className="font-medium">
              {approved ? "Approved" : "Rejected"}
              {run.verdict.reviewer ? ` by ${run.verdict.reviewer}` : ""}
            </p>
            {run.verdict.feedback && (
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                “{run.verdict.feedback}”
              </p>
            )}
            {run.publish.permalink && (
              <Button size="sm" variant="secondary" className="mt-3" asChild>
                <a href={run.publish.permalink} target="_blank" rel="noreferrer">
                  View on Instagram <ExternalLink />
                </a>
              </Button>
            )}
            {run.publish.error && (
              <p
                className="mt-3 rounded-[var(--radius-md)] px-3 py-2 text-sm"
                style={{
                  background: "var(--phase-failed-soft)",
                  color: "var(--phase-failed-fg)",
                }}
              >
                Publishing failed: {run.publish.error}
              </p>
            )}
          </div>
        </div>
      </Card>
    )
  }

  // --- state 3: a decision is being processed right now -------------------
  if (!run.pending_review) {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-3">
          <Loader2 className="size-4 animate-spin-slow text-[var(--muted-foreground)]" />
          <div>
            <p className="font-medium">A decision is being processed</p>
            <p className="text-sm text-[var(--muted-foreground)]">
              This run was just decided — possibly from Telegram. The pipeline
              is picking it up now.
            </p>
          </div>
        </div>
      </Card>
    )
  }

  // --- state 1: waiting for a human --------------------------------------
  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Chip tone="review" dot pulse>
          Waiting for your review
        </Chip>
        {run.review_round > 1 && <MutedChip>Round {run.review_round}</MutedChip>}
        {run.qa.passed === true && <Chip tone="qa">QA passed</Chip>}
      </div>

      {!publishConfigured && (
        <p
          className="mb-4 rounded-[var(--radius-md)] px-3 py-2 text-sm"
          style={{
            background: "var(--phase-review-soft)",
            color: "var(--phase-review-fg)",
          }}
        >
          Instagram is not configured (IG_USER_ID / IG_ACCESS_TOKEN). Approving
          will record the verdict, but publishing will fail.
        </p>
      )}

      {!rejecting && !confirming && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="brand" onClick={() => setConfirming(true)} disabled={busy}>
            <CheckCircle2 /> Approve &amp; publish
          </Button>
          <Button variant="ghost" onClick={() => setRejecting(true)} disabled={busy}>
            Reject
          </Button>
        </div>
      )}

      {confirming && (
        <div className="space-y-3">
          <p className="text-sm">
            This posts the carousel to Instagram publicly. Continue?
          </p>
          <div className="flex gap-2">
            <Button variant="brand" onClick={onApprove} disabled={busy}>
              {busy ? "Approving…" : "Yes, approve and publish"}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {rejecting && (
        <div className="space-y-3">
          <p className="text-sm font-medium">What exactly is not good?</p>
          <div className="flex flex-wrap gap-1.5">
            {REJECT_CATEGORIES.map((category) => {
              const on = picked.includes(category.key)
              return (
                <button
                  key={category.key}
                  type="button"
                  onClick={() =>
                    setPicked((p) =>
                      on ? p.filter((k) => k !== category.key) : [...p, category.key],
                    )
                  }
                  className="rounded-[var(--radius-pill)] border px-3 py-1 text-xs transition-colors"
                  style={
                    on
                      ? {
                          background: "var(--phase-rework-soft)",
                          color: "var(--phase-rework-fg)",
                          borderColor: "transparent",
                        }
                      : { borderColor: "var(--border)", color: "var(--muted-foreground)" }
                  }
                >
                  {category.label}
                </button>
              )
            })}
          </div>

          <Textarea
            rows={3}
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Say what is wrong, in your own words. This is routed to the agents that need to redo their work."
          />

          {predicted.length > 0 && (
            <p className="text-xs text-[var(--muted-foreground)]">
              Likely to re-run:{" "}
              {predicted.map((a) => AGENT_LABELS[a] ?? a).join(", ")}. The
              router decides for certain once it reads your feedback.
            </p>
          )}

          <div className="flex gap-2">
            <Button
              variant="destructive"
              onClick={() => {
                const categories = picked.length ? `[${picked.join(", ")}] ` : ""
                onReject(`${categories}${feedback.trim()}`.trim())
              }}
              disabled={busy || feedback.trim().length < 3}
            >
              {busy ? "Sending…" : "Reject and rework"}
            </Button>
            <Button variant="ghost" onClick={() => setRejecting(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
          <p className="text-xs text-[var(--muted-foreground)]">
            Feedback is required to reject — it is what tells the pipeline which
            parts to redo.
          </p>
        </div>
      )}
    </Card>
  )
}
