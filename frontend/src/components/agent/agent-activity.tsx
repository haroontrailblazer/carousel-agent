import * as React from "react"
import {
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  ExternalLink,
  Globe2,
  Image as ImageIcon,
  ListChecks,
  LoaderCircle,
  Search,
  Wrench,
} from "lucide-react"

import { AGENT_BLURBS, AGENT_LABELS } from "@/lib/pipeline"
import type { RunEvent, ToolCall, TraceSummary } from "@/lib/types"
import { cn } from "@/lib/utils"
import { LoadingState, type LoadingVariant } from "@/components/agent/loading-state"

type ThinkingView = "steps" | "reasoning" | "search" | "rendering"

const VIEWS: { value: ThinkingView; label: string; icon: typeof ListChecks }[] = [
  { value: "steps", label: "Steps", icon: ListChecks },
  { value: "reasoning", label: "Reasoning", icon: Brain },
  { value: "search", label: "Search", icon: Search },
  { value: "rendering", label: "Rendering", icon: ImageIcon },
]

const RENDER_AGENTS = new Set(["first_page_visual", "template_design", "cta"])
const SEARCH_TOOL = /search|fetch|source|download|scrape|url|media/i
const RENDER_TOOL = /render|image|cover|slide|stitch|artifact|video|caption/i

function compactDuration(ms: number | null | undefined): string {
  if (ms == null) return ""
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

function cleanEventText(text: string): string {
  return text.replace(/^\[[^\]]+\]\s*/, "").trim()
}

function eventThoughts(event: RunEvent): string[] {
  const thoughts = event.data?.thoughts
  return Array.isArray(thoughts)
    ? thoughts.filter((value): value is string => typeof value === "string" && !!value.trim())
    : []
}

function allTools(events: RunEvent[]): ToolCall[] {
  const tools: ToolCall[] = []
  const seen = new Set<string>()
  for (const event of events) {
    for (const tool of event.tools ?? []) {
      const key = tool.id || `${event.seq}:${tool.name}`
      if (seen.has(key)) continue
      seen.add(key)
      tools.push(tool)
    }
  }
  return tools
}

export function PixelLoader({
  label,
  live,
  outcome = "complete",
  variant = "Drive",
}: {
  label: string
  live: boolean
  outcome?: string
  variant?: LoadingVariant
}) {
  return (
    <div className="py-2">
      <LoadingState label={label} variant={variant} running={live} outcome={outcome} />
    </div>
  )
}

function ActivityStatus({ active, failed = false }: { active: boolean; failed?: boolean }) {
  if (failed) {
    return <Circle className="size-4 fill-[var(--destructive)] text-[var(--destructive)]" />
  }
  if (active) {
    return <LoaderCircle className="size-4 animate-spin-slow text-[var(--phase-generate)]" />
  }
  return (
    <span className="grid size-4 place-items-center rounded-full bg-[var(--brand)] text-[var(--brand-foreground)]">
      <Check className="size-2.5 stroke-[3]" />
    </span>
  )
}

function EmptyThinkingView({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-28 place-items-center px-5 py-7 text-center text-sm text-[var(--muted-foreground)]">
      {children}
    </div>
  )
}

export function ThinkingPanel({
  events,
  summary,
  live,
}: {
  events: RunEvent[]
  summary: TraceSummary | null
  live: boolean
}) {
  const [open, setOpen] = React.useState(true)
  const [view, setView] = React.useState<ThinkingView>("steps")

  const tools = React.useMemo(() => allTools(events), [events])
  const agentSteps = React.useMemo(() => {
    const firstSeen = new Map<string, RunEvent>()
    for (const event of events) {
      if (!event.author || event.author === "user" || event.author === "carousel_orchestrator") {
        continue
      }
      if (!firstSeen.has(event.author)) firstSeen.set(event.author, event)
    }
    const lastAuthor = [...firstSeen.keys()].at(-1)
    return [...firstSeen.entries()].map(([author, event]) => {
      const stat = summary?.agents.find((item) => item.name === author)
      return {
        id: `${event.seq}:${author}`,
        author,
        label: AGENT_LABELS[author] ?? author.replaceAll("_", " "),
        detail: AGENT_BLURBS[author] ?? cleanEventText(event.text),
        duration: compactDuration(stat?.ms),
        active: live && author === lastAuthor,
        failed: stat?.errors ? stat.errors > 0 : false,
      }
    })
  }, [events, live, summary])

  const thoughts = React.useMemo(
    () => events.flatMap((event) => eventThoughts(event).map((text) => ({ event, text }))),
    [events],
  )
  const searchTools = tools.filter((tool) => SEARCH_TOOL.test(tool.name))
  const renderTools = tools.filter((tool) => RENDER_TOOL.test(tool.name))
  const renderEvents = events.filter((event) => RENDER_AGENTS.has(event.author))
  const visibleSteps = agentSteps.length > 5 ? agentSteps.slice(-4) : agentSteps
  const hiddenStepCount = agentSteps.length - visibleSteps.length

  return (
    <section className="overflow-hidden rounded-[16px] border border-[var(--border)] bg-[var(--card)]/55">
      <div className="flex min-h-14 items-center border-b border-[var(--border)] px-3 sm:px-4">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex items-center gap-2 rounded-lg px-1.5 py-2 text-sm font-medium hover:bg-[var(--muted)]"
          aria-expanded={open}
        >
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          <Brain className="size-4" />
          Thinking
        </button>

        <div className="ml-auto hidden items-stretch self-stretch sm:flex" role="tablist" aria-label="Thinking detail">
          {VIEWS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={view === value}
              onClick={() => {
                setView(value)
                setOpen(true)
              }}
              className={cn(
                "relative px-3 text-xs text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]",
                view === value && "text-[var(--foreground)]",
              )}
            >
              {label}
              {view === value && <span className="absolute inset-x-2 bottom-0 h-px bg-[var(--foreground)]" />}
            </button>
          ))}
        </div>
      </div>

      {open && (
        <>
          <div className="flex gap-1 overflow-x-auto border-b border-[var(--border)] p-2 sm:hidden" role="tablist">
            {VIEWS.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={view === value}
                onClick={() => setView(value)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs",
                  view === value ? "bg-[var(--muted)] text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
                )}
              >
                <Icon className="size-3.5" /> {label}
              </button>
            ))}
          </div>

          {view === "steps" && (
            <div className="divide-y divide-[var(--border)]">
              {agentSteps.length ? (
                <>
                  {hiddenStepCount > 0 && (
                    <div className="flex items-center gap-3 px-4 py-2.5 text-xs text-[var(--muted-foreground)]">
                      <span className="grid size-4 place-items-center rounded-full bg-[var(--muted)]"><Check className="size-2.5" /></span>
                      {hiddenStepCount} earlier agent steps completed
                    </div>
                  )}
                  {visibleSteps.map((step) => (
                    <div key={step.id} className="flex items-start gap-3 px-4 py-3">
                      <span className="mt-0.5"><ActivityStatus active={step.active} failed={step.failed} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{step.label}</span>
                        <span className="mt-0.5 block text-xs leading-5 text-[var(--muted-foreground)]">{step.detail}</span>
                      </span>
                      <span className="pt-0.5 text-xs tabular-nums text-[var(--muted-foreground)]">
                        {step.active ? "running" : step.duration}
                      </span>
                    </div>
                  ))}
                </>
              ) : (
                <EmptyThinkingView>The orchestrator is preparing the first agent.</EmptyThinkingView>
              )}
            </div>
          )}

          {view === "reasoning" && (
            thoughts.length ? (
              <div className="max-h-72 space-y-3 overflow-y-auto p-4">
                {thoughts.slice(-8).map(({ event, text }, index) => (
                  <div key={`${event.seq}:${index}`} className="flex gap-3 text-sm leading-6">
                    <Brain className="mt-1 size-4 shrink-0 text-[var(--muted-foreground)]" />
                    <div>
                      <p>{text}</p>
                      <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                        {AGENT_LABELS[event.author] ?? event.author}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyThinkingView>Reasoning will appear when the model emits ADK thought parts.</EmptyThinkingView>
            )
          )}

          {view === "search" && (
            searchTools.length ? (
              <div className="divide-y divide-[var(--border)]">
                {searchTools.slice(-10).map((tool) => (
                  <ToolRow key={tool.id || tool.name} tool={tool} icon={Globe2} />
                ))}
              </div>
            ) : (
              <EmptyThinkingView>Search calls and retrieved sources will appear here.</EmptyThinkingView>
            )
          )}

          {view === "rendering" && (
            renderTools.length || renderEvents.length ? (
              <div className="divide-y divide-[var(--border)]">
                {renderTools.slice(-10).map((tool) => (
                  <ToolRow key={tool.id || tool.name} tool={tool} icon={ImageIcon} />
                ))}
                {!renderTools.length && renderEvents.slice(-6).map((event) => (
                  <div key={event.seq} className="flex items-center gap-3 px-4 py-3 text-sm">
                    <ImageIcon className="size-4 text-[var(--muted-foreground)]" />
                    <span className="min-w-0 flex-1 truncate">
                      {cleanEventText(event.text) || AGENT_BLURBS[event.author]}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyThinkingView>Cover and slide rendering activity will appear here.</EmptyThinkingView>
            )
          )}
        </>
      )}
    </section>
  )
}

function ToolRow({ tool, icon: Icon }: { tool: ToolCall; icon: typeof Wrench }) {
  const active = tool.status === "running"
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <Icon className="mt-0.5 size-4 shrink-0 text-[var(--muted-foreground)]" />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-xs font-medium">{tool.name}</span>
        {tool.args && (
          <span className="mt-1 line-clamp-2 block whitespace-pre-wrap text-xs leading-5 text-[var(--muted-foreground)]">
            {tool.args}
          </span>
        )}
      </span>
      <span className="flex items-center gap-1.5 text-xs tabular-nums text-[var(--muted-foreground)]">
        <ActivityStatus active={active} failed={tool.status === "error"} />
        {active ? "running" : compactDuration(tool.ms)}
      </span>
    </div>
  )
}

export function ToolChipList({ events }: { events: RunEvent[] }) {
  const tools = React.useMemo(() => allTools(events), [events])
  if (!tools.length) return null

  return (
    <div className="flex flex-wrap gap-2" aria-label={`${tools.length} tool calls`}>
      {tools.slice(-8).map((tool) => (
        <span
          key={tool.id || tool.name}
          className="inline-flex max-w-full items-center gap-2 rounded-[9px] border border-[var(--border)] bg-[var(--card)] px-2.5 py-1.5 text-xs"
          title={tool.args || tool.result || tool.name}
        >
          {SEARCH_TOOL.test(tool.name) ? <Globe2 className="size-3.5" /> : RENDER_TOOL.test(tool.name) ? <ImageIcon className="size-3.5" /> : <Wrench className="size-3.5" />}
          <span className="max-w-44 truncate font-mono">{tool.name}</span>
          <span className="tabular-nums text-[var(--muted-foreground)]">
            {tool.status === "running" ? "running" : compactDuration(tool.ms)}
          </span>
          {tool.status === "ok" ? (
            <Check className="size-3.5 text-[var(--phase-qa)]" />
          ) : tool.status === "running" ? (
            <LoaderCircle className="size-3.5 animate-spin-slow text-[var(--phase-generate)]" />
          ) : (
            <Circle className="size-3.5 fill-[var(--destructive)] text-[var(--destructive)]" />
          )}
        </span>
      ))}
    </div>
  )
}

function sourceUrls(events: RunEvent[]): string[] {
  const seen = new Set<string>()
  const urls: string[] = []
  for (const event of events) {
    const sources = event.data?.sources
    if (!Array.isArray(sources)) continue
    for (const source of sources) {
      if (typeof source !== "string" || seen.has(source)) continue
      seen.add(source)
      urls.push(source)
    }
  }
  return urls
}

function usefulMessages(events: RunEvent[]): { seq: number; text: string }[] {
  const messages: { seq: number; text: string }[] = []
  for (const event of events) {
    if (!event.text || event.author === "user" || event.author === "carousel_orchestrator") continue
    const text = cleanEventText(event.text)
    if (!text || text.startsWith("->") || text.startsWith("<-") || text.startsWith("{")) continue
    if (text.length < 28) continue
    messages.push({ seq: event.seq, text })
  }
  return messages.slice(-3)
}

export function StreamedAgentText({ events, live }: { events: RunEvent[]; live: boolean }) {
  const messages = usefulMessages(events)
  const sources = sourceUrls(events)
  if (!messages.length && !sources.length) return null

  return (
    <section className="space-y-4" aria-live="polite">
      {messages.map((message, index) => (
        <p key={message.seq} className="animate-line-reveal text-[15px] leading-7 text-[var(--foreground)]">
          {message.text}
          {index === messages.length - 1 && live && (
            <span className="ml-1 inline-block h-4 w-0.5 translate-y-0.5 animate-pulse bg-[var(--foreground)]" aria-label="Streaming" />
          )}
        </p>
      ))}

      {sources.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-[var(--muted-foreground)]">Sources</p>
          <div className="flex flex-wrap gap-2">
            {sources.slice(0, 6).map((source, index) => {
              let host = source
              try { host = new URL(source).hostname.replace(/^www\./, "") } catch { /* keep source */ }
              return (
                <a
                  key={source}
                  href={source}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex max-w-full items-center gap-2 rounded-[9px] border border-[var(--border)] bg-[var(--card)] px-2.5 py-1.5 text-xs transition-colors hover:bg-[var(--muted)]"
                >
                  <Globe2 className="size-3.5 shrink-0 text-[var(--link)]" />
                  <span className="max-w-52 truncate">{host}</span>
                  <span className="grid size-4 shrink-0 place-items-center rounded bg-[var(--muted)] text-[10px] text-[var(--muted-foreground)]">{index + 1}</span>
                  <ExternalLink className="size-3 shrink-0 text-[var(--muted-foreground)]" />
                </a>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
