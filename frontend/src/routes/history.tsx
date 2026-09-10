import * as React from "react"
import { StudioEmblem } from "@/components/layout/studio-emblem"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router"

import { TaskActions } from "@/components/run/task-actions"
import { Button } from "@/components/ui/button"
import { Chip } from "@/components/ui/chip"
import { Skeleton } from "@/components/ui/skeleton"
import { relativeTime } from "@/lib/format"
import { isRemembered, runsQuery } from "@/lib/queries"
import { PHASE_LABELS, STATUS_LABELS, STATUS_TOKEN } from "@/lib/pipeline"
import { runDetailChunk } from "@/lib/route-chunks"
import type { RunStatus } from "@/lib/types"
import { ArrowRight, ArrowUpRight, Layers, Plus, Search } from "lucide-react"
import { StudioArtwork } from "@/components/layout/studio-artwork"
import "./tasks.css"

const FILTERS: { label: string; value: RunStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Needs review", value: "awaiting_review" },
  { label: "Running", value: "running" },
  { label: "Published", value: "done" },
  { label: "Interrupted", value: "interrupted" },
  { label: "Failed", value: "failed" },
  { label: "Cancelled", value: "cancelled" },
]

/**
 * Task history.
 *
 * The list is fetched ONCE and filtered in the browser.
 *
 * Filtering on the server looked tidier but was the wrong trade at this size:
 * every chip click became a new React Query key, which means no cached data,
 * which means a full loading skeleton and another round trip. Against a remote
 * database that is ~0.6s locally and up to 2s over a tunnel - so clicking
 * through six filters cost six waits to re-render at most fifty rows the
 * browser already had.
 *
 * One query, one cache entry, instant chips. It also means the sidebar's
 * "needs review" badge can read the SAME cache entry instead of issuing its
 * own request on every page.
 */
export function useRuns() {
  return useQuery({
    // Query and snapshot both live in lib/queries.ts, so the sidebar can
    // prefetch EXACTLY what this page is about to ask for. A second copy of
    // these options here would prefetch a different cache entry and warm
    // nothing.
    ...runsQuery(),
    refetchInterval: (query) =>
      query.state.data?.items.some((r) =>
        ["running", "awaiting_review"].includes(r.status),
      )
        ? 15_000
        : 60_000,
  })
}


export function HistoryRoute() {
  const [filter, setFilter] = React.useState<RunStatus | "all">("all")
  const [search, setSearch] = React.useState("")
  const runs = useRuns()
  const all = runs.data?.items ?? []
  const items = React.useMemo(() => {
    const term = search.trim().toLowerCase()
    return all.filter(run => (filter === "all" || run.status === filter)
      && (!term || [run.title, run.run_id, run.source].some(value => value?.toLowerCase().includes(term))))
  }, [all, filter, search])
  const counts = React.useMemo(() => {
    const map: Record<string, number> = { all: all.length }
    for (const run of all) map[run.status] = (map[run.status] ?? 0) + 1
    return map
  }, [all])
  const firstLoad = runs.isLoading && !runs.data

  return (
    <div className="tasks-page">
      <header className="studio-page-heading tasks-heading">
        <StudioEmblem />
        <div className="tasks-heading-copy">
          <div className="flex items-baseline gap-2">
            <h1>Tasks</h1>
            {isRemembered(runs) && <span className="text-xs text-[var(--muted-foreground)]">refreshing…</span>}
          </div>
          <p>Your ideas, from first draft to final carousel.</p>
        </div>
        <Button variant="brand" size="sm" asChild><Link to="/new" viewTransition><Plus /> New carousel</Link></Button>
      </header>

      <section className="tasks-browser" aria-label="Browse tasks">
        <div className="tasks-toolbar">
          <div><strong>Your carousels</strong><span>{all.length} {all.length === 1 ? "task" : "tasks"}</span></div>
          <label className="tasks-search"><Search aria-hidden="true" />
            <input type="search" aria-label="Search tasks" placeholder="Search tasks…" value={search} onChange={event => setSearch(event.target.value)} />
          </label>
        </div>
        <div className="tasks-filters" role="group" aria-label="Filter tasks by status">
          {FILTERS.map(f => <button key={f.value} type="button" onClick={() => setFilter(f.value)} aria-pressed={filter === f.value}>
            {f.label}<span>{counts[f.value] ?? 0}</span>
          </button>)}
        </div>
      </section>

      {runs.isError && <div className="tasks-error" role="alert"><span>Couldn’t refresh your tasks. Try again to get the latest updates.</span><Button variant="secondary" size="sm" onClick={() => void runs.refetch()}>Try again</Button></div>}
      {firstLoad && <div className="tasks-grid" role="status" aria-label="Loading tasks">{[0,1,2,3].map(i => <Skeleton key={i} className="task-card-skeleton" />)}</div>}
      {!firstLoad && !runs.isError && items.length === 0 && (
        <div className="tasks-empty">
          <StudioArtwork name="carousel-sculpture" sizes="96px" />
          <h2>{search || filter !== "all" ? "No matching tasks" : "Your next idea starts here"}</h2>
          <p>{search || filter !== "all" ? "Try another search or choose a different status." : "Create a carousel and follow its progress here."}</p>
          {search || filter !== "all"
            ? <Button variant="secondary" size="sm" onClick={() => { setSearch(""); setFilter("all") }}>Clear filters</Button>
            : <Button variant="brand" size="sm" asChild><Link to="/new" viewTransition>Create a carousel <ArrowRight /></Link></Button>}
        </div>
      )}
      {items.length > 0 && <div className="tasks-grid" aria-label="Your tasks">
        {items.map(run => (
          <article key={run.run_id} className="task-card" aria-label={run.title || run.run_id} data-status={run.status}>
            <div className="task-card-top">
              <Chip tone={STATUS_TOKEN[run.status]} dot pulse={run.status === "running"}>{STATUS_LABELS[run.status]}</Chip>
              <time dateTime={run.created_at ?? undefined} title={run.created_at ?? undefined}>{relativeTime(run.created_at)}</time>
            </div>
            <Link to={'/tasks/' + run.run_id} viewTransition className="task-card-main"
              onPointerEnter={() => void runDetailChunk().catch(() => undefined)}>
              <div className="task-card-art" aria-hidden="true"><StudioArtwork name={run.phase === "generate" ? "research-lens" : run.phase === "rework" ? "design-stylus" : "carousel-sculpture"} sizes="64px" /></div>
              <div className="task-card-copy"><h2>{run.title || "Untitled carousel"}</h2>
                <p><Layers aria-hidden="true" />{PHASE_LABELS[run.phase] ?? run.phase}</p>
              </div>
            </Link>
            <div className="task-card-meta"><span>{run.source === "queue" ? "From newsroom" : run.source === "manual" ? "From your brief" : run.source || "Carousel"}</span>{run.review_round > 0 && <span>Review round {run.review_round}</span>}</div>
            <footer className="task-card-footer">
              <Link to={'/tasks/' + run.run_id} viewTransition className="task-open-link">Open task <ArrowUpRight aria-hidden="true" /></Link>
              <div className="task-card-actions">
                {run.status === "awaiting_review"
                  ? <Button variant="brand" size="sm" asChild><Link to={'/tasks/' + run.run_id + '?tab=review'} viewTransition>Review carousel <ArrowRight /></Link></Button>
                  : <TaskActions runId={run.run_id} status={run.status} title={run.title} />}
              </div>
            </footer>
          </article>
        ))}
      </div>}
    </div>
  )
}
