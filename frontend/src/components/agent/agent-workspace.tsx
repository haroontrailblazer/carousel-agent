/**
 * The live agent workspace: one implementation, two places.
 *
 * This is what the New carousel screen becomes once a run is under way - the
 * conversation, the asset rail, and a composer that turns into a Stop button
 * while the agents work. The task page's Chat tab renders the SAME component,
 * because "open the chat for this task" should give you the screen you were
 * just looking at, not a read-only transcript of it.
 *
 * Two variants, differing only in chrome:
 *
 * * `standalone` - /new. Owns the page: its own header, a full-height scroll
 *   pane, and a composer docked to the bottom of the viewport.
 * * `embedded` - the Chat tab. The task page already supplies a header and its
 *   own scrolling, so the composer sits inline at the end of the conversation
 *   and the assets run underneath rather than in a side rail.
 *
 * What is NOT a variant: which data it reads, how hard it polls, or what the
 * Stop button does. Those come from `useRunWorkspace` and are identical, which
 * is the whole point - the two screens cannot drift into disagreeing about the
 * state of one task.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router"
import { MoreHorizontal, Plus } from "lucide-react"
import { toast } from "sonner"

import { PixelLoader } from "@/components/agent/agent-activity"
import { AgentAssetRail, AgentAssetStrip } from "@/components/agent/agent-assets"
import { AgentComposer, type ComposerState } from "@/components/agent/agent-composer"
import { AgentConversation } from "@/components/agent/agent-conversation"
import { AgentActivityStatus } from "@/components/agent/agent-workspace-status"
import { invalidateRun, type RunWorkspace } from "@/hooks/use-run-workspace"
import { ApiError, post } from "@/lib/api"
import { AGENT_LABELS, PHASE_LABELS } from "@/lib/pipeline"
import type { RunDetail, RunEvent } from "@/lib/types"

function activeAgentLabel(events: RunEvent[]): string {
  for (let index = events.length - 1; index >= 0; index--) {
    const author = events[index].author
    if (author && author !== "user" && author !== "carousel_orchestrator") {
      return AGENT_LABELS[author] ?? author.replaceAll("_", " ")
    }
  }
  return "Preparing your carousel"
}

export function activityLabel(run: RunDetail, events: RunEvent[]): string {
  if (run.status === "awaiting_review") return "Your carousel is ready for review"
  if (run.status === "done") return "Carousel published"
  if (run.status === "cancelled") return "Task stopped"
  if (run.status === "failed") return "The carousel agent stopped"
  if (run.status === "interrupted") return "The background task was interrupted"
  const agent = activeAgentLabel(events)
  return `${agent} · ${(PHASE_LABELS[run.phase] ?? run.phase).toLowerCase()}`
}

/**
 * Which affordance the composer offers.
 *
 * Derived from the run rather than from local state, so a task decided on
 * another device lands on the right control here without a reload. `cancelled`
 * is grouped with the finished states rather than with `failed`: stopping a
 * task on purpose is not a failure, and the difference is what the button
 * beneath it says.
 */
export function composerStateFor(
  workspace: RunWorkspace,
  starting: boolean,
): ComposerState {
  const { run, isLive } = workspace
  if (starting || run.isLoading) return "starting"
  if (isLive) return "running"
  if (run.isError || !run.data) return "failed"
  return ["awaiting_review", "done", "cancelled"].includes(run.data.status)
    ? "complete"
    : "failed"
}

export function AgentWorkspace({
  runId,
  workspace,
  prompt,
  variant = "standalone",
  onReset,
}: {
  runId: string
  workspace: RunWorkspace
  /** What the person typed, when this browser is the one that typed it. */
  prompt?: string
  variant?: "standalone" | "embedded"
  /** Clear the workspace and go back to an empty composer. */
  onReset?: () => void
}) {
  const { run, artifacts, stream, isLive } = workspace
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const embedded = variant === "embedded"

  const cancel = useMutation({
    mutationFn: () => post(`/api/runs/${runId}/cancel`),
    onSuccess: () => {
      // Everything a stop changes, re-read together. Invalidating only the run
      // snapshot left the trace pulsing on a task that had already stopped.
      invalidateRun(queryClient, runId)
      toast.success("Stopping", { description: "The agents are being cancelled." })
    },
    onError: (error) => {
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "not_running") {
        // The task ended between the click and the request arriving.
        toast.info("Already stopped", { description: "That task is no longer running." })
        invalidateRun(queryClient, runId)
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not stop the task.")
    },
  })

  const state = composerStateFor(workspace, false)

  const conversation = (
    <>
      {run.isLoading ? (
        <PixelLoader label="Connecting to the task transcript…" live />
      ) : run.isError || !run.data ? (
        <div className="rounded-[14px] border border-[var(--phase-failed)]/35 bg-[var(--phase-failed-soft)] p-4 text-sm text-[var(--phase-failed-fg)]">
          <p className="font-medium">This task could not be loaded.</p>
          <p className="mt-1 opacity-90">
            It may have been deleted.{" "}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => (onReset ? onReset() : navigate("/new"))}
            >
              Start a new one
            </button>
            .
          </p>
        </div>
      ) : (
        <AgentConversation
          run={run.data}
          events={stream.events}
          summary={stream.summary}
          live={isLive}
          artifacts={artifacts.data}
          runId={runId}
          prompt={prompt}
          showReviewCta={!embedded}
        />
      )}
    </>
  )

  const composer = (
    <AgentComposer
      value=""
      onChange={() => undefined}
      onSubmit={() => undefined}
      // Stop stays available for the whole time the agents are up, including
      // the seconds before the first snapshot arrives - a run you cannot
      // cancel because the page has not finished loading is the worst moment
      // to be told to wait.
      onStop={() => cancel.mutate()}
      onReset={onReset}
      state={state}
      stopping={cancel.isPending}
    />
  )

  if (embedded) {
    return (
      <div className="space-y-6">
        <div className="space-y-6">{conversation}</div>
        <AgentAssetStrip artifacts={artifacts.data} live={isLive} runId={runId} />
        {composer}
      </div>
    )
  }

  return (
    <div className="agent-workspace-grid">
      <section className="agent-conversation-pane">
        <header className="agent-workspace-header">
          <div className="min-w-0">
            <h1 className="truncate font-[Georgia,serif] text-2xl font-normal tracking-[-0.025em] sm:text-[31px]">
              {run.data?.title || run.data?.news.title || (run.isError ? "Task unavailable" : "New carousel")}
            </h1>
            {run.data ? (
              <AgentActivityStatus
                status={run.data.status}
                label={activityLabel(run.data, stream.events)}
                connected={stream.connected}
              />
            ) : run.isError ? (
              // A permanent 404 is not a slow connection. Pulsing "connecting"
              // at someone forever, above a body that already says the task
              // could not be loaded, is the page arguing with itself.
              <p className="mt-1.5 text-xs text-[var(--muted-foreground)]">
                This task could not be loaded.
              </p>
            ) : (
              <p className="mt-1 flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                <span className="size-1.5 rounded-full bg-[var(--brand)] animate-pip-pulse" />
                Connecting to the carousel agent
              </p>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <NewChatButton />
            <Link
              to={`/tasks/${runId}`}
              viewTransition
              className="grid size-9 shrink-0 place-items-center rounded-[10px] text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
              title="Open the full task - trace, chat and review"
            >
              <MoreHorizontal className="size-5" />
              <span className="sr-only">Open full task details</span>
            </Link>
          </div>
        </header>

        <div className="agent-conversation-scroll">
          <div className="mx-auto w-full max-w-3xl space-y-6 px-5 pb-44 pt-6 sm:px-8 sm:pt-9">
            {conversation}
          </div>
        </div>

        <div className="agent-running-composer-dock">
          <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-8">{composer}</div>
        </div>
      </section>

      <AgentAssetRail
        artifacts={artifacts.data}
        loading={artifacts.isLoading}
        live={isLive}
        runId={runId}
      />
    </div>
  )
}

/**
 * Start another carousel without giving up this one.
 *
 * Several runs can be in flight at once, so this is not "abandon and restart" -
 * the task you are looking at keeps working, keeps streaming, and stays in
 * Tasks. It just stops being the thing on screen.
 */
export function NewChatButton({ className }: { className?: string }) {
  const navigate = useNavigate()
  return (
    <button
      type="button"
      onClick={() => navigate("/new", { viewTransition: true })}
      className={
        className ??
        "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-[10px] border border-[var(--border)] px-2.5 text-[13px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
      }
      title="Start another carousel - this one keeps running"
    >
      <Plus className="size-4" />
      <span className="hidden sm:inline">New chat</span>
      <span className="sr-only sm:hidden">New chat</span>
    </button>
  )
}
