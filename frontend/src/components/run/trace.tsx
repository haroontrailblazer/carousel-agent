import * as React from "react"
import { AlertTriangle, ChevronRight, Wrench } from "lucide-react"

import { Chip } from "@/components/ui/chip"
import { groupByAuthor } from "@/hooks/use-run-stream"
import {
  AGENT_BLURBS,
  AGENT_LABELS,
  PHASES,
  PHASE_LABELS,
} from "@/lib/pipeline"
import type { Phase, RunEvent } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * The phase rail.
 *
 * `rework` is drawn as a return arc back to `qa` rather than as the fourth
 * step in a line, because that is what it actually is: rejected work goes back
 * and comes round again. Drawing it as a straight progression would tell the
 * reader something false about how the pipeline behaves.
 */
export function PhaseRail({ phase, live }: { phase: Phase; live: boolean }) {
  const linear = PHASES.filter((p) => p !== "rework")
  const activeIndex = linear.indexOf(phase === "rework" ? "qa" : phase)

  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
      {linear.map((p, index) => {
        const done = index < activeIndex
        const active = index === activeIndex
        return (
          <React.Fragment key={p}>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-2.5 py-1 text-xs font-medium",
                !done && !active && "text-[var(--muted-foreground)]",
              )}
              style={
                done || active
                  ? {
                      backgroundColor: `var(--phase-${p}-soft)`,
                      color: `var(--phase-${p}-fg)`,
                    }
                  : undefined
              }
            >
              <span
                aria-hidden
                className={cn(
                  "size-1.5 rounded-full",
                  active && live && "animate-pip-pulse",
                )}
                style={{
                  backgroundColor:
                    done || active ? `var(--phase-${p})` : "var(--border)",
                }}
              />
              {PHASE_LABELS[p]}
            </span>
            {index < linear.length - 1 && (
              <span aria-hidden className="text-[var(--border)]">
                ·
              </span>
            )}
          </React.Fragment>
        )
      })}

      {phase === "rework" && (
        <Chip tone="rework" dot pulse={live} className="ml-1">
          ↻ Reworking, then back to checking
        </Chip>
      )}
    </div>
  )
}

function ShimmerBar() {
  return (
    <span
      aria-hidden
      className="animate-shimmer block h-0.5 w-full rounded-full"
    />
  )
}

function toolNames(event: RunEvent): string[] {
  const calls = event.data?.tool_calls
  const responses = event.data?.tool_responses
  const out: string[] = []
  if (Array.isArray(calls)) out.push(...(calls as string[]))
  if (Array.isArray(responses)) out.push(...(responses as string[]))
  return out
}

function AgentGroup({
  author,
  events,
  active,
  defaultOpen,
}: {
  author: string
  events: RunEvent[]
  active: boolean
  defaultOpen: boolean
}) {
  const [open, setOpen] = React.useState(defaultOpen)
  const failed = events.some((e) => e.kind === "error")
  const tools = Array.from(new Set(events.flatMap(toolNames)))
  const label = AGENT_LABELS[author] ?? author

  return (
    <div
      className={cn(
        "rounded-[var(--radius)] border bg-[var(--card)] transition-colors",
        failed ? "border-[var(--destructive)]/40" : "border-[var(--border)]",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
        aria-expanded={open}
      >
        <span
          aria-hidden
          className={cn("size-2 shrink-0 rounded-full", active && "animate-pip-pulse")}
          style={{
            backgroundColor: failed
              ? "var(--destructive)"
              : active
                ? "var(--phase-generate)"
                : "var(--brand)",
          }}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate font-medium">{label}</span>
            {failed && (
              <AlertTriangle className="size-3.5 text-[var(--destructive)]" />
            )}
          </span>
          {active && (
            <span className="mt-1.5 block">
              <ShimmerBar />
            </span>
          )}
        </span>

        {tools.length > 0 && (
          <span className="hidden items-center gap-1 sm:flex">
            <Wrench className="size-3 text-[var(--muted-foreground)]" />
            <span className="text-xs text-[var(--muted-foreground)]">
              {tools.length}
            </span>
          </span>
        )}
        <span className="text-xs text-[var(--muted-foreground)]">
          {events.length}
        </span>
        <ChevronRight
          className={cn(
            "size-4 shrink-0 text-[var(--muted-foreground)] transition-transform",
            open && "rotate-90",
          )}
        />
      </button>

      {open && (
        <div className="space-y-2 border-t border-[var(--border)] px-4 py-3">
          {AGENT_BLURBS[author] && (
            <p className="text-xs text-[var(--muted-foreground)]">
              {AGENT_BLURBS[author]}
            </p>
          )}
          {events.map((event) => (
            <div
              key={event.seq}
              className="animate-line-reveal font-mono text-xs leading-relaxed"
            >
              <span
                className={cn(
                  "whitespace-pre-wrap break-words",
                  event.kind === "error"
                    ? "text-[var(--destructive)]"
                    : "text-[var(--foreground)]",
                )}
              >
                {event.text || (event.kind === "error"
                  ? String(event.data?.error ?? "error")
                  : "…")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The run trace.
 *
 * Events are grouped by the agent that produced them, in arrival order, so a
 * rework round naturally appears as the same agent showing up again further
 * down rather than being merged into its first appearance.
 *
 * Text is displayed verbatim from the server. The STRUCTURE (which phase,
 * which round) comes from each event's data payload, never from parsing this
 * text - see app/runs/stream.py.
 */
export function AgentTrace({
  events,
  live,
  synced,
}: {
  events: RunEvent[]
  live: boolean
  synced: boolean
}) {
  const groups = React.useMemo(() => groupByAuthor(events), [events])

  if (!synced && events.length === 0) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-14 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--muted)]"
          />
        ))}
      </div>
    )
  }

  if (events.length === 0) {
    return (
      <p className="rounded-[var(--radius)] border border-dashed border-[var(--border)] p-6 text-center text-sm text-[var(--muted-foreground)]">
        No trace recorded for this run. Runs started from the CLI or the ADK dev
        UI do not write to this timeline.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {groups.map((group, index) => (
        <AgentGroup
          key={`${group.author}-${index}`}
          author={group.author}
          events={group.events}
          active={live && index === groups.length - 1}
          defaultOpen={index === groups.length - 1}
        />
      ))}
    </div>
  )
}
