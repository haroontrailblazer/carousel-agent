import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams } from "react-router"
import { ExternalLink, RotateCcw, WifiOff } from "lucide-react"
import { toast } from "sonner"

import { AgentTrace, PhaseRail } from "@/components/run/trace"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Chip, MutedChip } from "@/components/ui/chip"
import { useRunStream } from "@/hooks/use-run-stream"
import { get, post } from "@/lib/api"
import { compactNumber, elapsed, relativeTime } from "@/lib/format"
import { PHASE_LABELS, STATUS_LABELS, STATUS_TOKEN } from "@/lib/pipeline"
import type { RunDetail } from "@/lib/types"

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
    refetchInterval: (query) =>
      query.state.data && ["running", "awaiting_review"].includes(query.state.data.status)
        ? 10_000
        : false,
  })

  const stream = useRunStream(runId, {
    onPhase: () => void queryClient.invalidateQueries({ queryKey: ["run", runId] }),
    onEnd: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
    },
  })

  const resume = useMutation({
    mutationFn: () => post(`/api/runs/${runId}/resume`),
    onSuccess: () => {
      toast.success("Resuming from where it stopped")
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not resume"),
  })

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
        <p className="font-medium">Run not found</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          It may have been removed, or the id is wrong.
        </p>
        <Button className="mt-4" variant="ghost" asChild>
          <Link to="/runs">Back to runs</Link>
        </Button>
      </Card>
    )
  }

  const data = run.data
  const live = data.status === "running"
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

        <h1 className="text-xl font-semibold tracking-tight">
          {data.title || data.news.title || data.run_id}
        </h1>

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
            <p className="font-medium">This run was interrupted</p>
            <p className="text-sm text-[var(--muted-foreground)]">
              The service restarted while it was working. It can pick up from
              the start of the “{PHASE_LABELS[data.phase] ?? data.phase}” phase.
            </p>
          </div>
          <Button onClick={() => resume.mutate()} disabled={resume.isPending}>
            <RotateCcw /> {resume.isPending ? "Resuming…" : "Resume"}
          </Button>
        </Card>
      )}

      {data.pending_review && (
        <Card className="flex flex-wrap items-center gap-4 p-4">
          <div className="min-w-0 flex-1">
            <p className="font-medium">Ready for review</p>
            <p className="text-sm text-[var(--muted-foreground)]">
              {data.slide_count} slides and a cover are waiting for a decision.
            </p>
          </div>
          <Button variant="brand" asChild>
            <Link to={`/runs/${data.run_id}/review`}>Review it</Link>
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

      <section className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-base font-semibold tracking-tight">Trace</h2>
          <div className="flex gap-2 text-xs text-[var(--muted-foreground)]">
            {tokens.llm_calls != null && <span>{tokens.llm_calls} LLM calls</span>}
            {tokens.image_calls != null && <span>· {tokens.image_calls} images</span>}
            {tokens.total_tokens != null && (
              <span>· {compactNumber(tokens.total_tokens)} tokens</span>
            )}
          </div>
        </div>
        <AgentTrace events={stream.events} live={live} synced={stream.synced} />
      </section>
    </div>
  )
}
