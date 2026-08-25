import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams, useSearchParams } from "react-router"
import { ExternalLink, Images, ListTree, MessagesSquare, WifiOff } from "lucide-react"

import {
  AgentConversation,
  startedFromComposer,
} from "@/components/agent/agent-conversation"
import { ReviewPanel } from "@/components/review/review-panel"
import { TaskActions } from "@/components/run/task-actions"
import { AgentTrace, PhaseRail } from "@/components/run/trace"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Chip, MutedChip } from "@/components/ui/chip"
import { TabPanel, Tabs } from "@/components/ui/tabs"
import { useRunStream } from "@/hooks/use-run-stream"
import { get } from "@/lib/api"
import { elapsed, relativeTime } from "@/lib/format"
import { PHASE_LABELS, STATUS_LABELS, STATUS_TOKEN } from "@/lib/pipeline"
import type { RunArtifacts, RunDetail, RunStatus } from "@/lib/types"

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
  const queryClient = useQueryClient()

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => get<RunDetail>(`/api/runs/${runId}`),
    // Poll while the run is going. The stream carries the trace, but the
    // authoritative snapshot (bundle, pending_review, publish result) lives
    // here - and pending_review is not monotonic, so a one-time read is not
    // enough.
    // Phase, pending_review and the publish result all change under the
    // user's feet while a run works; 10s was slow enough that the trace moved
    // on before the header caught up.
    // awaiting_review polls harder than it used to because the decision card
    // now lives on this screen: the same task can be decided from Telegram,
    // and this snapshot is what notices.
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === "running") return 4_000
      if (status === "awaiting_review") return 8_000
      return false
    },
    refetchIntervalInBackground: false,
  })

  // Derived here rather than after the loading guard: the trace hook needs it
  // to decide how hard to poll, and a finished task must not be polled at all.
  const isLive = run.data?.status === "running"

  // Same query key the Review tab uses, so opening Chat costs no extra
  // request - React Query serves both from one cache entry.
  const artifacts = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => get<RunArtifacts>(`/api/runs/${runId}/artifacts`),
    enabled: !!runId,
    staleTime: 60_000,
  })

  const stream = useRunStream(runId, {
    live: isLive,
    onPhase: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      // The bundle is written at a phase boundary, and rework rewrites it.
      // Without this the Review tab keeps showing the previous round.
      void queryClient.invalidateQueries({ queryKey: ["artifacts", runId] })
    },
    onEnd: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
    },
  })

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

  // A gap means the browser fell behind and lost events; the database still
  // has them, so refetch rather than showing an incomplete trace.
  React.useEffect(() => {
    if (stream.gapped) {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
    }
  }, [stream.gapped, queryClient, runId])

  if (run.isLoading) {
    return <div className="h-64 animate-pulse rounded-[var(--radius)] bg-[var(--muted)]" />
  }
  if (run.isError || !run.data) {
    return (
      <Card className="p-6">
        <p className="font-medium">Task not found</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          It may have been removed, or the id is wrong.
        </p>
        <Button className="mt-4" variant="ghost" asChild>
          <Link to="/tasks">Back to tasks</Link>
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
          {!stream.connected && stream.synced && (
            <MutedChip>
              <WifiOff className="size-3" /> Reconnecting
            </MutedChip>
          )}
        </div>

        <div className="flex flex-wrap items-start justify-between gap-3">
          <h1 className="text-xl font-semibold tracking-tight">
            {data.title || data.news.title || data.run_id}
          </h1>
          <TaskActions runId={data.run_id} status={data.status} title={data.title} />
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
          <Card className="p-5">
            <AgentConversation
              run={data}
              events={stream.events}
              summary={stream.summary}
              live={live}
              artifacts={artifacts.data}
              runId={runId}
              // No prompt in local state here: this task may have been started
              // in another session entirely, so the component reconstructs it
              // from the run itself.
              showReviewCta={false}
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
