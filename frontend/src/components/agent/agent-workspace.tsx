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

import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router"
import { ListTree, Pencil, Plus } from "lucide-react"
import { toast } from "sonner"

import { AgentAssetRail, AgentAssetStrip } from "@/components/agent/agent-assets"
import { AgentComposer, type ComposerState } from "@/components/agent/agent-composer"
import { AgentConversation } from "@/components/agent/agent-conversation"
import { Skeleton } from "@/components/ui/skeleton"
import { InlineEdit } from "@/components/ui/inline-edit"
import { useRenameRun } from "@/hooks/use-rename-run"
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
  // `pending_review`, not the status: it is the authoritative "is a decision
  // still wanted?" flag, and it is NOT monotonic - a failed resume restores
  // the pending row. This is the only state where a typed message has a
  // paused tool call to answer, so it has to be read from the flag that
  // actually tracks one.
  if (run.data.pending_review) return "review"
  return ["awaiting_review", "done", "cancelled"].includes(run.data.status)
    ? "complete"
    : "failed"
}

/**
 * The chat's name, renameable in place.
 *
 * A task is named by the pipeline the moment it starts, from whatever was
 * typed or fetched, and those names are serviceable rather than yours - "the
 * new viral news in AI" is what you asked for, not what you would call it a
 * week later when you are looking for it in a list of forty.
 *
 * Editing happens where the name is, at the size it is displayed, rather than
 * in a dialog: what you type is what will be there. The heading keeps its
 * exact type while being edited for the same reason.
 *
 * Only for a task that exists. There is nothing to rename before one has been
 * started, and the empty composer's heading is a greeting, not a name.
 */
function ChatTitle({
  runId,
  run,
  isError,
}: {
  runId: string | null
  run: RunDetail | undefined
  isError: boolean
}) {
  const rename = useRenameRun()
  const [editing, setEditing] = React.useState(false)

  const shown = run?.title || run?.news.title || "Task unavailable"
  // Small, and in the interface font rather than the display serif.
  //
  // This used to be a 31px Georgia headline, which made sense when it sat in
  // a tall bar of its own. Without the bar it is a label on a row of controls,
  // and the same name is already the highlighted row in the sidebar two
  // inches to the left - so restating it in the largest type on the screen was
  // spending the page's most valuable line on something the user just clicked.
  const typography = "truncate text-sm font-semibold tracking-tight"

  // Nothing is known yet: hold the line's space and say nothing. Falling back
  // to "New carousel" here was the bug - it named a task that already has a
  // name, for as long as the request took.
  if (!run) {
    if (isError) return <h1 className={typography}>Task unavailable</h1>
    return <Skeleton className="h-5 w-64 max-w-full" />
  }
  if (!runId) return <h1 className={typography}>{shown}</h1>

  if (editing) {
    return (
      <h1 className={typography}>
        <InlineEdit
          value={run.title ?? ""}
          placeholder={run.news.title || runId}
          label="Rename this chat"
          onCommit={(next) => {
            if (next.trim() !== (run.title ?? "").trim()) {
              rename.mutate({ runId, title: next })
            }
            setEditing(false)
          }}
          onCancel={() => setEditing(false)}
          className="w-full"
        />
      </h1>
    )
  }

  return (
    <h1 className={typography}>
      <button
        type="button"
        onClick={() => setEditing(true)}
        title="Rename this chat"
        className="group/title inline-flex max-w-full items-center gap-2 text-left"
      >
        <span className="truncate">{shown}</span>
        <Pencil
          aria-hidden
          className="size-4 shrink-0 text-[var(--muted-foreground)] opacity-0 transition-opacity group-hover/title:opacity-100 group-focus-visible/title:opacity-100 [@media(hover:none)]:opacity-60"
        />
        <span className="sr-only">Rename this chat</span>
      </button>
    </h1>
  )
}

/**
 * The chat, before any of it has arrived.
 *
 * Shaped like what replaces it - a prompt bubble on the right, then the
 * agent's status line, its thinking panel and the first steps under it - so
 * the page does not rearrange itself the moment the data lands.
 *
 * It deliberately contains NO text. The old loading state said "Connecting to
 * the task transcript…" above a heading that read "New carousel", and both
 * were guesses: the task is already connected as far as the user is concerned
 * (they clicked a chat that exists), and its name is whatever the server is
 * about to say, not "New carousel". Showing a wrong title and then correcting
 * it is worse than showing no title at all, because the wrong one is
 * indistinguishable from a real answer for as long as it is up.
 */
function ChatSkeleton() {
  return (
    <div className="space-y-6" aria-hidden>
      <div className="flex justify-end gap-3">
        <Skeleton className="h-12 w-[62%] rounded-[16px]" />
        <Skeleton className="size-8 shrink-0 rounded-full" />
      </div>

      <div className="space-y-5">
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-40 rounded-[14px]" />
        <div className="flex flex-wrap gap-2">
          <Skeleton className="h-7 w-32 rounded-full" />
          <Skeleton className="h-7 w-28 rounded-full" />
          <Skeleton className="h-7 w-36 rounded-full" />
        </div>
        <Skeleton className="h-4 w-[70%]" />
        <Skeleton className="h-4 w-[54%]" />
      </div>
    </div>
  )
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

  /**
   * What happens to a message typed after the agents have stopped.
   *
   * Two destinations, and which one it is depends entirely on whether the
   * pipeline still has a paused step to answer.
   *
   * **Waiting on review.** The orchestrator is suspended inside
   * `await_human_review`. Rejecting with feedback answers that call, and the
   * rework phase runs `learner` and `feedback_router` over the text before
   * re-running only the agents it names. So a sentence typed here genuinely
   * reaches the agents and changes the carousel - it is the same channel the
   * Reject box on the review tab uses, which is why it posts the same verdict
   * rather than inventing a second path to keep in step with it.
   *
   * **Anything after that.** Published, failed or cancelled runs have no
   * suspended call left, and the root agent is a phase state machine rather
   * than a chat partner - handed text at `done` it emits its closing summary
   * and stops. So a message here starts a NEW carousel, which is what someone
   * typing a fresh topic into a finished chat means anyway.
   */
  const [followUp, setFollowUp] = React.useState("")

  const rework = useMutation({
    mutationFn: (feedback: string) =>
      post(`/api/runs/${runId}/verdict`, { status: "rejected", feedback }),
    onSuccess: () => {
      setFollowUp("")
      invalidateRun(queryClient, runId)
      toast.success("Sent to the agents", {
        description: "Reworking the carousel with your notes.",
      })
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not send that.",
      ),
  })

  const startAnother = useMutation({
    mutationFn: (text: string) =>
      post<{ run_id: string; title: string }>(
        "/api/runs",
        /^https?:\/\/\S+$/i.test(text)
          ? { source: "url", url: text }
          : { source: "topic", topic: text },
      ),
    onSuccess: (data) => {
      setFollowUp("")
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      // Pushed, not replaced: the chat that was on screen is a real place the
      // user may want to go back to.
      navigate(`/new?run=${encodeURIComponent(data.run_id)}`, {
        viewTransition: true,
      })
      toast.success("Your carousel is cooking", { description: data.title })
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not start that.",
      ),
  })

  const sendFollowUp = React.useCallback(() => {
    const text = followUp.trim()
    if (text.length < 3 || rework.isPending || startAnother.isPending) return
    if (state === "review") rework.mutate(text)
    else startAnother.mutate(text)
  }, [followUp, state, rework, startAnother])


  /**
   * Nothing real goes on screen until everything real is here.
   *
   * "Everything" is the run AND its trace. The run alone gives the title and
   * the status, but the body of the page is the transcript - so rendering as
   * soon as the run lands produced a titled, empty chat that then filled in,
   * which is two loading states in a row rather than one.
   *
   * Artifacts are deliberately NOT waited on. That endpoint 404s for most of a
   * run's life by design - the carousel is only assembled at the end - so
   * waiting for it would hold the skeleton up for fifteen minutes on a task
   * that is working perfectly.
   */
  const booting = !run.isError && (!run.data || !stream.synced)

  const conversation = (
    <>
      {booting ? (
        <ChatSkeleton />
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
      value={followUp}
      onChange={setFollowUp}
      onSubmit={sendFollowUp}
      // Stop stays available for the whole time the agents are up, including
      // the seconds before the first snapshot arrives - a run you cannot
      // cancel because the page has not finished loading is the worst moment
      // to be told to wait.
      onStop={() => cancel.mutate()}
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
          {/* The title alone. The activity line that used to sit under it -
              "Your carousel is ready for review", "Connecting to the carousel
              agent" - is not lost: the conversation below says all three of
              those things already, in its own status row, its loader and its
              error card. In the header it was a second copy of whatever the
              top of the transcript was saying. */}
          <div className="min-w-0">
            <ChatTitle runId={runId} run={run.data} isError={run.isError} />
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <NewChatButton />
            {/* The trace, named as the trace.
                
                This was a three-dot overflow control, which promises "more
                options" and delivered one destination. The same icon the task
                page uses for its Trace tab says where the click goes before
                it is made, and `?tab=trace` opens on that tab rather than on
                whichever one the task's status would have chosen. */}
            <Link
              to={`/tasks/${runId}?tab=trace`}
              viewTransition
              className="grid size-9 shrink-0 place-items-center rounded-[10px] text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
              title="Open the agent trace"
            >
              <ListTree className="size-4.5" />
              <span className="sr-only">Open the agent trace</span>
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
