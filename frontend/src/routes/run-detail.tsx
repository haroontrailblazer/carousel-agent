import * as React from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router"
import { ExternalLink, Images, ListTree, MessagesSquare, WifiOff } from "lucide-react"

import { startedFromComposer } from "@/components/agent/agent-conversation"
import { AgentWorkspace, NewChatButton } from "@/components/agent/agent-workspace"
import { ReviewPanel } from "@/components/review/review-panel"
import { TaskActions } from "@/components/run/task-actions"
import { AgentTrace, PhaseRail } from "@/components/run/trace"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Chip, MutedChip } from "@/components/ui/chip"
import { TabPanel, Tabs } from "@/components/ui/tabs"
import { useRunWorkspace } from "@/hooks/use-run-workspace"
import { elapsed, relativeTime } from "@/lib/format"
import { PHASE_LABELS, STATUS_LABELS, STATUS_TOKEN } from "@/lib/pipeline"
import type { RunStatus } from "@/lib/types"

type TaskTab = "trace" | "chat" | "review"

/**
 * Which view a task opens on.
 *
 * A task that is still working is a process to watch, so it opens on the
 * trace. A task that is waiting for a decision, or already published, is a
 * carousel to look at, so it opens on the review. Everything else - failed,
 * cancelled, interrupted - opens on the trace, because that is where the
 * explanation is.
 */
function defaultTab(status: RunStatus): TaskTab {
  return status === "awaiting_review" || status === "done" ? "review" : "trace"
}

export function RunDetailRoute() {
  const { runId = "" } = useParams()
  const navigate = useNavigate()

  // The SAME hook the New carousel screen uses. Both screens read the same
  // three cache entries, and React Query keys the cache by key alone - so two
  // sets of options for one key means whichever screen mounted last decides
  // how both behave. One hook, one answer.
  const workspace = useRunWorkspace(runId)
  const { run, stream, isLive } = workspace

  // --- which tab ----------------------------------------------------------
  // Resolved once, on arrival, then pinned. The status moves under the user -
  // a task reaches review while they are reading the trace - and yanking the
  // screen out from under someone mid-read is not helpful. The Review tab
  // pulses instead, and they switch when they are ready.
  const [params, setParams] = useSearchParams()
  const urlTab = params.get("tab")
  const [tab, setTab] = React.useState<TaskTab | null>(
    urlTab === "review" || urlTab === "trace" || urlTab === "chat"
      ? urlTab
      : null,
  )
  const status = run.data?.status

  React.useEffect(() => {
    if (tab || !status) return
    setTab(defaultTab(status))
  }, [tab, status])

  const active: TaskTab = tab ?? "trace"

  const selectTab = React.useCallback(
    (next: TaskTab) => {
      setTab(next)
      // Kept in the query string rather than the path: changing the path
      // remounts this route, which would tear down and rebuild the event
      // stream on every tab click.
      setParams(
        (prev) => {
          const copy = new URLSearchParams(prev)
          copy.set("tab", next)
          return copy
        },
        { replace: true },
      )
    },
    [setParams],
  )


  if (run.isLoading) {
    // Laid out like the header, tab bar and body that are about to replace it,
    // so the page does not visibly rearrange itself the moment data lands.
    return (
      <div className="space-y-6">
        <header className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Skeleton className="h-6 w-24 rounded-full" />
            <Skeleton className="h-6 w-20 rounded-full" />
          </div>
          <Skeleton className="h-7 w-2/3" />
          <Skeleton className="h-4 w-40" />
        </header>
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-64 rounded-[var(--radius)]" />
      </div>
    )
  }
  if (run.isError || !run.data) {
    return (
      <Card className="p-6">
        <p className="font-medium">Task not found</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          It may have been removed, or the id is wrong.
        </p>
        <Button className="mt-4" variant="ghost" asChild>
          <Link to="/tasks" viewTransition>Back to tasks</Link>
        </Button>
      </Card>
    )
  }

  const data = run.data
  const live = isLive
  const tokens = data.token_usage ?? {}

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={STATUS_TOKEN[data.status]} dot pulse={live}>
            {STATUS_LABELS[data.status]}
          </Chip>
          <MutedChip>{PHASE_LABELS[data.phase] ?? data.phase}</MutedChip>
          {data.rework_round > 0 && <MutedChip>Rework {data.rework_round}</MutedChip>}
          {/* Only when the view has genuinely lost track of the run.
              This used to key off the SSE connection with no `live` guard,
              which meant two wrong things at once: every tunnelled session
              showed it (Cloudflare buffers SSE, so the stream never opens
              even though polling is fine), and a FINISHED task showed it
              permanently - there is no stream to connect to once a run ends,
              so `connected` stayed false forever on an immutable trace. */}
          {stream.stale && (
            <MutedChip
              title="Could not reach the server for the latest trace; retrying."
              style={{
                background: "var(--phase-failed-soft)",
                color: "var(--phase-failed-fg)",
              }}
            >
              <WifiOff className="size-3" /> Reconnecting
            </MutedChip>
          )}
        </div>

        <div className="flex flex-wrap items-start justify-between gap-3">
          <h1 className="text-xl font-semibold tracking-tight">
            {data.title || data.news.title || data.run_id}
          </h1>
          <div className="flex items-center gap-1.5">
            {/* Several carousels can run at once, so starting another does not
                cost you this one - it keeps working and stays in Tasks. */}
            <NewChatButton />
            <TaskActions runId={data.run_id} status={data.status} title={data.title} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--muted-foreground)]">
          <span className="font-mono">{data.run_id}</span>
          <span>started {relativeTime(data.created_at)}</span>
          <span>· {elapsed(data.created_at, live ? null : data.updated_at)}</span>
          {data.requested_by && <span>· by {data.requested_by}</span>}
          {data.news.source_url && (
            <a
              className="inline-flex items-center gap-1 text-[var(--link)] hover:underline"
              href={data.news.source_url}
              target="_blank"
              rel="noreferrer"
            >
              source <ExternalLink className="size-3" />
            </a>
          )}
        </div>
      </header>

      <PhaseRail phase={data.phase} live={live} />

      {data.status === "interrupted" && (
        <Card className="flex flex-wrap items-center gap-4 p-4">
          <div className="min-w-0 flex-1">
            <p className="font-medium">This task was interrupted</p>
            <p className="text-sm text-[var(--muted-foreground)]">
              The service restarted while it was working. It can pick up from
              the start of the “{PHASE_LABELS[data.phase] ?? data.phase}” phase.
            </p>
          </div>
        </Card>
      )}

      {/* Only on the trace tab: on the review tab the decision card below is
          already saying this, louder. */}
      {data.pending_review && active === "trace" && (
        <Card className="flex flex-wrap items-center gap-4 p-4">
          <div className="min-w-0 flex-1">
            <p className="font-medium">Ready for review</p>
            <p className="text-sm text-[var(--muted-foreground)]">
              {data.slide_count} slides and a cover are waiting for a decision.
            </p>
          </div>
          <Button variant="brand" onClick={() => selectTab("review")}>
            Review it
          </Button>
        </Card>
      )}

      {data.qa.issues.length > 0 && (
        <Card className="p-4">
          <p className="mb-2 text-sm font-semibold">QA found {data.qa.issues.length} issue(s)</p>
          <ul className="space-y-1 text-sm">
            {data.qa.issues.map((issue, i) => (
              <li key={i} className="flex gap-2">
                <MutedChip>{issue.severity}</MutedChip>
                <span>
                  {issue.slide_index != null && (
                    <span className="text-[var(--muted-foreground)]">
                      slide {issue.slide_index}:{" "}
                    </span>
                  )}
                  {issue.message}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Tabs
            label="Task views"
            value={active}
            onChange={selectTab}
            items={[
              { value: "trace", label: "Trace", icon: <ListTree /> },
              // Only for tasks that were actually typed into the composer.
              // A queue or scheduled run had no conversation, and an empty
              // "Chat" tab would be a promise the task cannot keep.
              ...(startedFromComposer(data)
                ? [
                    {
                      value: "chat" as const,
                      label: "Chat",
                      icon: <MessagesSquare />,
                    },
                  ]
                : []),
              {
                value: "review",
                label: "Review",
                icon: <Images />,
                // The dot is the only reason a person would switch tabs
                // unprompted, so it appears when - and only when - a decision
                // is actually wanted.
                badge: data.pending_review ? (
                  <span
                    aria-label="waiting for your decision"
                    className="size-1.5 rounded-full animate-pip-pulse"
                    style={{ backgroundColor: "var(--phase-review)" }}
                  />
                ) : null,
              },
            ]}
          />
          {active === "trace" && (
            <div className="flex gap-2 text-xs text-[var(--muted-foreground)]">
              {tokens.llm_calls != null && <span>{tokens.llm_calls} LLM calls</span>}
              {tokens.image_calls != null && <span>· {tokens.image_calls} images</span>}
            </div>
          )}
        </div>

        <TabPanel value="trace" selected={active === "trace"}>
          <AgentTrace
            events={stream.events}
            summary={stream.summary}
            live={live}
            synced={stream.synced}
          />
        </TabPanel>

        <TabPanel value="chat" selected={active === "chat"}>
          {/* The same workspace as the New carousel screen, minus its own
              header and viewport-docked composer - this page supplies both.
              Opening Chat should give you the screen you were just working
              in, not a read-only transcript of it. */}
          <Card className="p-5">
            <AgentWorkspace
              runId={runId}
              workspace={workspace}
              variant="embedded"
              // No prompt in local state here: this task may have been started
              // in another session entirely, so the component reconstructs it
              // from the run itself.
              onReset={() => navigate("/new", { viewTransition: true })}
            />
          </Card>
        </TabPanel>

        <TabPanel value="review" selected={active === "review"}>
          <ReviewPanel run={data} />
        </TabPanel>
      </section>
    </div>
  )
}
