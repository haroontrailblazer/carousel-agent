import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Chip, MutedChip } from "@/components/ui/chip"
import { get } from "@/lib/api"
import { relativeTime } from "@/lib/format"
import { PHASE_LABELS, STATUS_LABELS, STATUS_TOKEN } from "@/lib/pipeline"
import type { RunStatus, RunSummary } from "@/lib/types"
import { cn } from "@/lib/utils"

const FILTERS: { label: string; value: RunStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Needs review", value: "awaiting_review" },
  { label: "Running", value: "running" },
  { label: "Published", value: "done" },
  { label: "Interrupted", value: "interrupted" },
  { label: "Failed", value: "failed" },
]

export function HistoryRoute() {
  const [filter, setFilter] = React.useState<RunStatus | "all">("all")

  const runs = useQuery({
    queryKey: ["runs", filter],
    queryFn: () =>
      get<{ items: RunSummary[] }>(
        `/api/runs?limit=50${filter === "all" ? "" : `&status=${filter}`}`,
      ),
    // Keep the list moving while anything is live, and stop when nothing is.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) =>
        ["running", "awaiting_review"].includes(r.status),
      )
        ? 15_000
        : false,
  })

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Runs</h1>
        <Button variant="brand" size="sm" asChild>
          <Link to="/new">New carousel</Link>
        </Button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFilter(f.value)}
            className={cn(
              "rounded-[var(--radius-pill)] border px-3 py-1 text-xs font-medium transition-colors",
              filter === f.value
                ? "border-transparent bg-[var(--foreground)] text-[var(--background)]"
                : "border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--muted)]",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {runs.isLoading && (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-20 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--muted)]"
            />
          ))}
        </div>
      )}

      {runs.data?.items.length === 0 && (
        <Card className="p-10 text-center">
          <p className="font-medium">Nothing here yet</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {filter === "all"
              ? "Start a run and it will show up here."
              : "No runs match that filter."}
          </p>
          {filter === "all" && (
            <Button variant="brand" className="mt-4" asChild>
              <Link to="/new">Make one</Link>
            </Button>
          )}
        </Card>
      )}

      <div className="space-y-2">
        {runs.data?.items.map((run) => (
          <Card key={run.run_id} glide className="p-4">
            <Link to={`/runs/${run.run_id}`} className="block">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">
                    {run.title || <span className="font-mono text-sm">{run.run_id}</span>}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Chip
                      tone={STATUS_TOKEN[run.status]}
                      dot
                      pulse={run.status === "running"}
                    >
                      {STATUS_LABELS[run.status]}
                    </Chip>
                    <MutedChip>{PHASE_LABELS[run.phase] ?? run.phase}</MutedChip>
                    {run.source && <MutedChip>{run.source}</MutedChip>}
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {relativeTime(run.created_at)}
                    </span>
                  </div>
                </div>

                {run.status === "awaiting_review" && (
                  <Button
                    variant="brand"
                    size="sm"
                    asChild
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Link to={`/runs/${run.run_id}/review`}>Review</Link>
                  </Button>
                )}
              </div>
            </Link>
          </Card>
        ))}
      </div>
    </div>
  )
}
