import * as React from "react"
import { AlertTriangle, ChevronRight, Clock, Coins, Wrench } from "lucide-react"

import { Chip } from "@/components/ui/chip"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { groupByAuthor } from "@/hooks/use-run-stream"
import { compactNumber } from "@/lib/format"
import { AGENT_BLURBS, AGENT_LABELS, PHASES, PHASE_LABELS } from "@/lib/pipeline"
import type { Phase, RunEvent, ToolCall, TraceSummary } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * A duration in the units it deserves.
 *
 * Per-agent and per-tool timings are mostly seconds, and "00:00:04" spends
 * six characters saying almost nothing while burying the one digit that
 * matters. Compact units keep the significant figure at the front, where the
 * eye lands. Totals are the exception - see `clock` below.
 */
function duration(ms: number | null | undefined): string {
  if (ms == null) return "—"
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  if (ms < 3_600_000) return `${m}m ${s.toString().padStart(2, "0")}s`
  // The orchestrator's block brackets the pause for review, so it really can
  // span hours. "1440m 00s" is not a number anyone can read.
  return `${Math.floor(m / 60)}h ${(m % 60).toString().padStart(2, "0")}m`
}

/**
 * hh:mm:ss - for the run TOTAL only.
 *
 * A total is the one number people read as a clock ("how long did this take?")
 * and compare between runs, so a fixed clock shape is right there. It is wrong
 * for the rows underneath, where the values span four orders of magnitude and
 * padding them all to the same width hides the differences.
 */
function clock(ms: number | null | undefined): string {
  if (ms == null) return "—"
  const total = Math.max(0, Math.round(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => n.toString().padStart(2, "0")
  return `${pad(h)}:${pad(m)}:${pad(s)}`
}

/** Slow things should look slow without the reader doing arithmetic. */
function heatTone(ms: number | null | undefined): string {
  if (ms == null) return "qa"
  if (ms >= 60_000) return "failed"
  if (ms >= 15_000) return "review"
  return "qa"
}

/**
 * The phase rail.
 *
 * `rework` is drawn as a return arc back to `qa` rather than as the fourth
 * step in a line, because that is what it actually is: rejected work goes back
 * and comes round again.
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

/**
 * One tool call as a pill, with its wall-clock cost on the face of it.
 *
 * The latency belongs ON the pill rather than inside the tooltip: "which tool
 * was slow" is the question people open a trace to answer, and making them
 * hover over fourteen pills to find out defeats the purpose. The tooltip
 * carries the detail - full arguments and the response - which you want only
 * once you have found the pill worth looking at.
 */
/**
 * One tool call, with its arguments and result behind a tooltip.
 *
 * Memoised because there are a lot of these - a finished run has dozens - and
 * each one mounts a Radix tooltip. While a run is live the trace is refetched
 * every three seconds, so without this every pill in the history rebuilds its
 * tooltip on every poll to render exactly what it rendered before.
 *
 * The default shallow comparison is the right one here: React Query does
 * structural sharing, so a `tool` object that did not change between two
 * responses comes back as the SAME object, and this bails out.
 */
const ToolPill = React.memo(function ToolPill({ tool }: { tool: ToolCall }) {
  const tone =
    tool.status === "error"
      ? "failed"
      : tool.status === "running"
        ? "generate"
        : heatTone(tool.ms)

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="inline-flex max-w-full items-center gap-1.5 rounded-[var(--radius-pill)] px-2.5 py-1 text-xs font-medium leading-none transition-transform hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          style={{
            backgroundColor: `var(--phase-${tone}-soft)`,
            color: `var(--phase-${tone}-fg)`,
          }}
        >
          <Wrench className="size-3 shrink-0" />
          <span className="truncate font-mono">{tool.name}</span>
          {tool.status === "running" ? (
            <span className="opacity-70">running…</span>
          ) : (
            <span className="tabular-nums opacity-80">{duration(tool.ms)}</span>
          )}
        </button>
      </TooltipTrigger>

      <TooltipContent side="top" align="start">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-4 border-b border-[var(--border)] pb-1.5">
            <span className="font-mono text-[13px] font-semibold">{tool.name}</span>
            <span className="flex items-center gap-2">
              <span className="tabular-nums text-[var(--muted-foreground)]">
                {duration(tool.ms)}
              </span>
              <Chip tone={tool.status === "error" ? "failed" : "done"}>
                {tool.status}
              </Chip>
            </span>
          </div>

          {tool.args && (
            <div>
              <p className="mb-1 font-semibold text-[var(--muted-foreground)]">
                Arguments
              </p>
              <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--muted)] p-2 font-mono text-[11px] leading-relaxed">
                {tool.args}
              </pre>
            </div>
          )}

          {tool.result && (
            <div>
              <p className="mb-1 font-semibold text-[var(--muted-foreground)]">
                Result
              </p>
              <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--muted)] p-2 font-mono text-[11px] leading-relaxed">
                {tool.result}
              </pre>
            </div>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  )
})

/** The run's cost and duration at a glance, above the per-agent detail. */
export function TraceSummaryBar({
  summary,
  live,
}: {
  summary: TraceSummary | null | undefined
  live: boolean
}) {
  if (!summary) return null
  const stats = [
    {
      key: "ms",
      icon: Clock,
      label:
        (summary.invocations ?? 1) > 1
          ? `Agent time · ${summary.invocations} runs`
          : "Agent time",
      value: clock(summary.ms),
      title:
        summary.span_ms != null
          ? `Time the agents actually ran, excluding waits for review. ` +
            `Wall clock since it started: ${clock(summary.span_ms)}.`
          : "Time the agents actually ran, excluding waits for review.",
    },
    {
      key: "tok",
      icon: Coins,
      label: "Tokens",
      value: summary.tokens ? compactNumber(summary.tokens.total) : "—",
      title: summary.tokens
        ? `${summary.tokens.prompt.toLocaleString()} in · ${summary.tokens.output.toLocaleString()} out`
        : undefined,
    },
    {
      key: "tools",
      icon: Wrench,
      label: "Tool calls",
      value: String(summary.tool_calls ?? 0),
    },
  ]

  return (
    <div className="grid grid-cols-3 gap-2">
      {stats.map(({ key, icon: Icon, label, value, title }) => (
        <div
          key={key}
          title={title}
          className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)] px-3 py-2"
        >
          <div className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
            <Icon className="size-3" />
            {label}
          </div>
          <div className="mt-0.5 flex items-baseline gap-1.5">
            <span className="text-lg font-semibold leading-none tabular-nums">
              {value}
            </span>
            {live && key === "ms" && (
              <span
                aria-hidden
                className="size-1.5 animate-pip-pulse rounded-full bg-[var(--phase-generate)]"
              />
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

type GroupStat = {
  ms: number | null
  tokens: { prompt: number; output: number; total: number }
  toolCalls: number
}

/**
 * Timing and cost for ONE appearance of an agent, from its own events.
 *
 * Looking these up by agent name was wrong: an agent appears in the trace once
 * per contiguous block of its events, and the orchestrator interleaves with
 * every child - so it shows up four or five times in a single run, and a
 * rework round brings agents back again. Keying on the name gave every one of
 * those blocks the SAME aggregate, which for the orchestrator meant each of
 * its appearances claiming the entire run's duration.
 *
 * The per-agent totals still have their place - that is what the summary bar
 * reports - but a row in the timeline must describe the segment it actually
 * represents.
 */
function groupStat(events: RunEvent[]): GroupStat {
  const tokens = { prompt: 0, output: 0, total: 0 }
  let toolCalls = 0
  let first: number | null = null
  let last: number | null = null

  for (const event of events) {
    const stamp = event.ts ?? event.created_at
    if (stamp) {
      const t = new Date(stamp).getTime()
      if (!Number.isNaN(t)) {
        if (first === null || t < first) first = t
        if (last === null || t > last) last = t
      }
    }
    const tok = event.data?.tokens as
      | { prompt?: number; output?: number; total?: number }
      | undefined
    if (tok) {
      tokens.prompt += tok.prompt ?? 0
      tokens.output += tok.output ?? 0
      tokens.total += tok.total ?? 0
    }
    toolCalls += (event.tools ?? []).filter((t) => t.args !== "").length
  }

  return {
    ms: first !== null && last !== null ? last - first : null,
    tokens,
    toolCalls,
  }
}

/**
 * A string that changes whenever anything a block RENDERS changes.
 *
 * Paired with the memo on AgentGroup - see the comment there for why the
 * events array itself cannot be the comparison.
 */
function blockSignature(
  author: string,
  events: RunEvent[],
  stat: GroupStat,
): string {
  let textChars = 0
  let toolChars = 0
  for (const event of events) {
    textChars += event.text?.length ?? 0
    for (const tool of event.tools ?? []) {
      toolChars +=
        tool.name.length + (tool.args?.length ?? 0) + (tool.result?.length ?? 0)
    }
  }
  return [
    author,
    events[0]?.seq ?? "",
    events[events.length - 1]?.seq ?? "",
    events.length,
    stat.ms ?? "",
    stat.tokens.total,
    stat.toolCalls,
    textChars,
    toolChars,
  ].join("|")
}

function ShimmerBar() {
  return (
    <span aria-hidden className="animate-shimmer block h-0.5 w-full rounded-full" />
  )
}

type AgentGroupProps = {
  author: string
  events: RunEvent[]
  stat: GroupStat
  slowestMs: number
  occurrence: number
  totalOccurrences: number
  active: boolean
  defaultOpen: boolean
  /** Everything about this block that can change. See the memo below. */
  signature: string
}

function AgentGroupImpl({
  author,
  events,
  stat,
  slowestMs,
  occurrence,
  totalOccurrences,
  active,
  signature,
  defaultOpen,
}: AgentGroupProps) {
  const [open, setOpen] = React.useState(defaultOpen)
  // Derived from the events, so they are recomputed when the events change and
  // not once per poll. `signature` is the dependency rather than `events`:
  // grouping rebuilds its arrays every time, so the array identity changes
  // even when not one frame in it did.
  const { failed, tools } = React.useMemo(
    () => ({
      failed: events.some((e) => e.kind === "error"),
      tools: events.flatMap((e) => e.tools ?? []),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signature],
  )
  const base = AGENT_LABELS[author] ?? author
  // An agent that runs more than once (the orchestrator between every child,
  // or any agent in a rework round) needs its passes told apart.
  const label =
    totalOccurrences > 1 ? `${base} · pass ${occurrence}` : base

  // A proportional bar makes "which agent ate the run" readable at a glance,
  // which a column of numbers does not.
  const share =
    stat.ms && slowestMs ? Math.min(100, Math.max(2, (stat.ms / slowestMs) * 100)) : 0

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
            {failed && <AlertTriangle className="size-3.5 text-[var(--destructive)]" />}
          </span>

          {share > 0 && !active && (
            <span className="mt-1.5 block h-1 w-full overflow-hidden rounded-full bg-[var(--muted)]">
              <span
                className="animate-rail-fill block h-full rounded-full"
                style={{
                  width: `${share}%`,
                  backgroundColor: `var(--phase-${heatTone(stat.ms)})`,
                }}
              />
            </span>
          )}
          {active && (
            <span className="mt-1.5 block">
              <ShimmerBar />
            </span>
          )}
        </span>

        <span className="hidden items-center gap-3 text-xs text-[var(--muted-foreground)] sm:flex">
          {stat.tokens.total ? (
            <span
              className="tabular-nums"
              title={`${stat.tokens.prompt.toLocaleString()} in · ${stat.tokens.output.toLocaleString()} out`}
            >
              {compactNumber(stat.tokens.total)} tok
            </span>
          ) : null}
          {stat.toolCalls ? (
            <span className="tabular-nums">{stat.toolCalls} calls</span>
          ) : null}
        </span>

        <span className="w-[4.5rem] shrink-0 text-right text-xs font-medium tabular-nums">
          {active ? (
            // No number while it is still working. The elapsed time of an
            // unfinished agent is not its duration - it is however long it has
            // been going so far, and showing that in the same column as
            // finished agents invites reading it as a total. The timer appears
            // when the agent stops.
            <span
              className="inline-flex items-center gap-1.5 font-normal"
              style={{ color: "var(--phase-generate-fg)" }}
            >
              <span
                aria-hidden
                className="size-1.5 animate-pip-pulse rounded-full"
                style={{ backgroundColor: "var(--phase-generate)" }}
              />
              running
            </span>
          ) : (
            duration(stat.ms)
          )}
        </span>

        <ChevronRight
          className={cn(
            "size-4 shrink-0 text-[var(--muted-foreground)] transition-transform",
            open && "rotate-90",
          )}
        />
      </button>

      {open && (
        <div className="space-y-3 border-t border-[var(--border)] px-4 py-3">
          {AGENT_BLURBS[author] && (
            <p className="text-xs text-[var(--muted-foreground)]">
              {AGENT_BLURBS[author]}
            </p>
          )}

          {tools.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {tools.map((tool, i) => (
                <ToolPill key={`${tool.id || tool.name}-${i}`} tool={tool} />
              ))}
            </div>
          )}

          <div className="space-y-1.5">
            {events.map((event) => {
              const text =
                event.text ||
                (event.kind === "error" ? String(event.data?.error ?? "error") : "")
              if (!text) return null
              return (
                <div
                  key={event.seq}
                  className="animate-line-reveal flex gap-2 font-mono text-xs leading-relaxed"
                >
                  <span className="shrink-0 select-none tabular-nums text-[var(--muted-foreground)]">
                    {String(event.seq).padStart(2, "0")}
                  </span>
                  <span
                    className={cn(
                      "min-w-0 whitespace-pre-wrap break-words",
                      event.kind === "error"
                        ? "text-[var(--destructive)]"
                        : "text-[var(--foreground)]",
                    )}
                  >
                    {text}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Re-render a block only when something about IT changed.
 *
 * This is the hot path of the whole console. While a run is live the trace is
 * refetched every three seconds for up to fifteen minutes, and each response
 * carries the entire history - so by the end of a run, a poll that adds one
 * frame was re-rendering every agent group and every tool pill that came
 * before it, several hundred times over.
 *
 * `events` cannot drive the comparison: grouping allocates a fresh array per
 * response, so the props are never shallow-equal and `React.memo` alone would
 * bail out of nothing. `signature` is built in `AgentTrace` from the things
 * that actually identify a block - which agent, which frames, how long, how
 * many tokens - and a trace is append-only, so a block whose signature is
 * unchanged genuinely rendered the same content.
 *
 * `defaultOpen` is deliberately not compared. It seeds `useState` on mount and
 * is ignored afterwards, so a change to it can never affect what is on screen,
 * and including it would defeat the memo on every append.
 */
const AgentGroup = React.memo(AgentGroupImpl, (prev, next) => {
  return (
    prev.signature === next.signature &&
    prev.slowestMs === next.slowestMs &&
    prev.active === next.active
  )
})

/**
 * The run trace.
 *
 * Frames come from ADK's own event transcript - the same log the /dev
 * inspector renders - so every task has a trace regardless of which surface
 * started it, and what is shown matches the inspector rather than
 * approximating it.
 *
 * Text is displayed verbatim from the server. The STRUCTURE (phase, tokens,
 * tool latency) comes from each event's data payload, never from parsing that
 * text - see app/runs/stream.py.
 */
export function AgentTrace({
  events,
  summary,
  live,
  synced,
}: {
  events: RunEvent[]
  summary?: TraceSummary | null
  live: boolean
  synced: boolean
}) {
  // Each contiguous block of one agent's events, with the stats for THAT
  // block - not for every time the agent ran.
  const blocks = React.useMemo(() => {
    const grouped = groupByAuthor(events)
    const seen: Record<string, number> = {}
    const counts: Record<string, number> = {}
    for (const g of grouped) counts[g.author] = (counts[g.author] ?? 0) + 1
    return grouped.map((g) => {
      seen[g.author] = (seen[g.author] ?? 0) + 1
      const stat = groupStat(g.events)
      return {
        ...g,
        stat,
        occurrence: seen[g.author],
        totalOccurrences: counts[g.author],
        // What this block IS, as a string that changes if any of it changes.
        //
        // Sequence numbers bound the frames and the count catches anything
        // inserted between them, which covers the ordinary case: a trace is
        // append-only and ADK never renumbers.
        //
        // The two lengths cover the case that is not append-only. A frame can
        // be REVISED in place - text streamed in against a seq that already
        // exists, or a tool call whose result lands after the call itself was
        // recorded. Neither moves a sequence number or a count, so without
        // these the memo would hold a half-written frame on screen and never
        // correct it. Summing lengths is O(1) per string and catches any edit
        // that changes what is rendered.
        signature: blockSignature(g.author, g.events, stat),
      }
    })
  }, [events])

  // Scale the bars against the slowest SEGMENT, excluding the orchestrator -
  // it interleaves with every child, so its segments bracket the others and
  // would otherwise squash every bar flat.
  const slowestMs = React.useMemo(
    () =>
      Math.max(
        1,
        ...blocks
          .filter((b) => b.author !== "carousel_orchestrator")
          .map((b) => b.stat.ms ?? 0),
      ),
    [blocks],
  )

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
        No trace for this task. Its agent transcript is gone — most often
        because the session was deleted from the agent inspector.
      </p>
    )
  }

  return (
    <TooltipProvider delayDuration={120} skipDelayDuration={300}>
      <div className="space-y-3">
        <TraceSummaryBar summary={summary} live={live} />

        <div className="space-y-2">
          {blocks.map((block, index) => (
            <AgentGroup
              key={`${block.author}-${index}`}
              author={block.author}
              events={block.events}
              stat={block.stat}
              slowestMs={slowestMs}
              occurrence={block.occurrence}
              totalOccurrences={block.totalOccurrences}
              signature={block.signature}
              active={live && index === blocks.length - 1}
              defaultOpen={index === blocks.length - 1}
            />
          ))}
        </div>

        {summary?.agents?.length ? (
          <p className="text-center text-[11px] text-[var(--muted-foreground)]">
            Hover a tool pill for its arguments and result.
          </p>
        ) : null}
      </div>
    </TooltipProvider>
  )
}
