import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, MoreHorizontal } from "lucide-react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"

import { PixelLoader, StreamedAgentText, ThinkingPanel, ToolChipList } from "@/components/agent/agent-activity"
import { AgentAssetRail, AgentAssetStrip } from "@/components/agent/agent-assets"
import { AgentComposer, type ComposerState } from "@/components/agent/agent-composer"
import { AgentActivityStatus } from "@/components/agent/agent-workspace-status"
import { BrandLogo } from "@/components/layout/brand-logo"
import { useRunStream } from "@/hooks/use-run-stream"
import { ApiError, get, post } from "@/lib/api"
import { AGENT_LABELS, PHASE_LABELS } from "@/lib/pipeline"
import type { RunArtifacts, RunDetail } from "@/lib/types"

/**
 * Headlines for the empty state, one picked at random per visit.
 *
 * All ten ask the same question - "what are we making a carousel about?" -
 * without ever naming the mechanism. The page already shows a composer and
 * suggestion chips, so spending the largest type on the screen explaining
 * what the screen does is a waste of it.
 *
 * Kept as a flat list rather than generated: these are voice, and voice is
 * written, not computed.
 */
const GREETINGS = [
  "What’s the story?",
  "Got a story?",
  "What’s worth posting?",
  "What’s making noise today?",
  "What caught your eye?",
  "What should we cover?",
  "What’s breaking?",
  "Give me a headline.",
  "What’s worth a carousel?",
  "What are we posting today?",
] as const

const SUGGESTIONS = [
  "the new viral news in AI",
  "this week's biggest AI product launch",
  "a developer tool that just shipped something notable",
]

function looksLikeUrl(value: string): boolean {
  return /^https?:\/\/\S+$/i.test(value.trim())
}

function activeAgentLabel(events: ReturnType<typeof useRunStream>["events"]): string {
  for (let index = events.length - 1; index >= 0; index--) {
    const author = events[index].author
    if (author && author !== "user" && author !== "carousel_orchestrator") {
      return AGENT_LABELS[author] ?? author.replaceAll("_", " ")
    }
  }
  return "Preparing your carousel"
}

function activityLabel(run: RunDetail, events: ReturnType<typeof useRunStream>["events"]): string {
  if (run.status === "awaiting_review") return "Your carousel is ready for review"
  if (run.status === "done") return "Carousel published"
  if (run.status === "cancelled") return "Task cancelled"
  if (run.status === "failed") return "The carousel agent stopped"
  if (run.status === "interrupted") return "The background task was interrupted"
  const agent = activeAgentLabel(events)
  return `${agent} · ${(PHASE_LABELS[run.phase] ?? run.phase).toLowerCase()}`
}

/** The New carousel screen is the synchronized ADK agent workspace itself. */
export function NewRunRoute() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const runId = params.get("run")
  const [value, setValue] = React.useState("")
  // Chosen once per mount, via a lazy initialiser rather than inline in the
  // JSX. This component re-renders on every keystroke in the composer, so an
  // inline Math.random() would reshuffle the headline as you type.
  const [greeting] = React.useState(
    () => GREETINGS[Math.floor(Math.random() * GREETINGS.length)],
  )
  const [submittedPrompt, setSubmittedPrompt] = React.useState("")
  const isUrl = looksLikeUrl(value)

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => get<RunDetail>(`/api/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) => query.state.data?.status === "running" ? 4_000 : false,
    refetchIntervalInBackground: false,
  })
  const isLive = run.data?.status === "running"

  const stream = useRunStream(runId, {
    live: isLive,
    onPhase: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["artifacts", runId] })
    },
    onEnd: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["artifacts", runId] })
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
    },
  })

  const artifacts = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => get<RunArtifacts>(`/api/runs/${runId}/artifacts`),
    enabled: !!runId,
    retry: false,
    refetchInterval: isLive ? 4_000 : false,
    refetchIntervalInBackground: false,
  })

  const start = useMutation({
    mutationFn: (payload: { source: string; topic?: string; url?: string }) =>
      post<{ run_id: string; title: string }>("/api/runs", payload),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      setSubmittedPrompt(value.trim())
      setParams({ run: data.run_id }, { replace: true })
      toast.success("Your carousel is cooking", { description: data.title })
    },
    onError: (error) => {
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "too_many_active_runs") {
        toast.error("One at a time", {
          description: "A carousel is already being made. Open it from Tasks or wait for review.",
        })
        return
      }
      if (code === "daily_limit_reached") {
        toast.error("Daily limit reached", {
          description: "Raise MAX_RUNS_PER_DAY to allow more today.",
        })
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not start that carousel.")
    },
  })

  const cancel = useMutation({
    mutationFn: () => post(`/api/runs/${runId}/cancel`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      toast.success("Task stopped")
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Could not stop the task."),
  })

  function submit() {
    const trimmed = value.trim()
    if (trimmed.length < 3) return
    start.mutate(isUrl ? { source: "url", url: trimmed } : { source: "topic", topic: trimmed })
  }

  function reset() {
    setValue("")
    setSubmittedPrompt("")
    setParams({}, { replace: true })
  }

  const composerState: ComposerState = start.isPending || (!!runId && run.isLoading)
    ? "starting"
    : isLive
      ? "running"
      : runId && run.data && ["awaiting_review", "done"].includes(run.data.status)
        ? "complete"
        : runId
          ? "failed"
          : "idle"

  if (!runId) {
    return (
      <div className="agent-empty-workspace">
        <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-4 py-12 sm:px-8">
          <div className="w-full">
            {/* The TITLE is centred on the page, not the title-plus-logo
                group. Centring the pair as a flex row pushes the words right
                of centre by half the mark's width, so the heading no longer
                lines up with the composer beneath it.

                So the mark is taken out of flow (absolute, right-full) and
                hung off the text's left edge. It cannot affect where the
                words land, however wide it gets.

                Sized in em rather than pixels: the img inherits the h1's
                font-size, so 1.30438em is one line of type plus 30% and holds
                at both the 30px and the 42px step. */}
            {/* Two different centrings, on purpose.

                At every width the WORDS are what sits on the page's centre
                line: the spacer mirrors the mark, so the group's own width
                cannot push the text off centre.

                On a phone the title also never wraps, and that is why the
                type is fluid rather than fixed. The mark and its mirror cost
                two mark-widths of room, so clamp() shrinks the type just
                enough to keep the longest greeting on one line - down to a
                280px screen - and caps at 24px so it does not grow on a big
                phone. A fixed size cannot do both: whatever value fits a
                390px iPhone overflows a 360px Android. */}
            <h1 className="mb-7 flex items-center justify-center whitespace-nowrap text-center font-[Georgia,serif] text-[clamp(16px,calc(7.1vw_-_3.5px),24px)] font-normal tracking-[-0.035em] sm:whitespace-normal sm:text-[42px] sm:leading-tight">
              {/* 8px gap on a phone, 12px from sm up. The desktop spacer below still
                  mirrors mr-3, so the words stay on the page's centre line there. */}
              <BrandLogo className="mr-2 size-[1.30438em] sm:mr-3" />
              <span className="min-w-0">{greeting}</span>
              {/* Optical centring on phones: the mirror is HALF the mark's width.

                  Neither exact answer looks right, and both are genuinely off.
                  Call L the mark plus its gap. Centre the words and the whole
                  composition sits L/2 left of centre - it reads as leaning
                  left. Centre the composition and the words sit L/2 right of
                  centre - the title reads as leaning right. The eye is
                  tracking both at once, so it disagrees with either.

                  Reserving L/2 on the right splits the error: the words land
                  L/4 right of centre, the composition L/4 left of it, and
                  neither is far enough off to register. Desktop keeps the full
                  mirror - with that much empty page around it, the words being
                  exactly centred is what reads as correct. */}
              <span aria-hidden className="ml-1 w-[0.65219em] shrink-0 sm:ml-3 sm:w-[1.30438em]" />
            </h1>

            <AgentComposer value={value} onChange={setValue} onSubmit={submit} state={composerState} />

            <div className="mt-4 flex flex-wrap justify-center gap-2" aria-label="Suggested prompts">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={start.isPending}
                  onClick={() => setValue(suggestion)}
                  className="rounded-[10px] border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-left text-xs text-[var(--muted-foreground)] transition-[background-color,color,border-color,transform] hover:-translate-y-px hover:border-[color-mix(in_oklch,var(--foreground)_18%,var(--border))] hover:bg-[var(--muted)] hover:text-[var(--foreground)] disabled:translate-y-0 disabled:cursor-default disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>

            <p className="mt-5 text-center text-[11px] leading-5 text-[var(--muted-foreground)]">
              Carousel Factory can make mistakes. Every carousel pauses for human review before publishing.
            </p>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="agent-workspace-grid">
      <section className="agent-conversation-pane">
        <header className="agent-workspace-header">
          <div className="min-w-0">
            <h1 className="truncate font-[Georgia,serif] text-2xl font-normal tracking-[-0.025em] sm:text-[31px]">
              {run.data?.title || run.data?.news.title || "New carousel"}
            </h1>
            {run.data ? (
              <AgentActivityStatus status={run.data.status} label={activityLabel(run.data, stream.events)} connected={stream.connected} />
            ) : (
              <p className="mt-1 flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                <span className="size-1.5 rounded-full bg-[var(--brand)] animate-pip-pulse" />
                Connecting to the carousel agent
              </p>
            )}
          </div>
          <Link
            to={`/tasks/${runId}`}
            className="grid size-9 shrink-0 place-items-center rounded-[10px] text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
            title="Open full task details"
          >
            <MoreHorizontal className="size-5" />
            <span className="sr-only">Open full task details</span>
          </Link>
        </header>

        <div className="agent-conversation-scroll">
          <div className="mx-auto w-full max-w-3xl space-y-6 px-5 pb-44 pt-6 sm:px-8 sm:pt-9">
            <div className="flex justify-end gap-3">
              <div className="max-w-[82%] rounded-[16px] bg-[var(--muted)] px-4 py-3 text-sm leading-6">
                {submittedPrompt || (run.data?.source === "url" ? run.data.news.source_url : `Create a carousel about ${run.data?.title ?? "this story"}`)}
              </div>
              <span className="mt-1 grid size-8 shrink-0 place-items-center rounded-full bg-[var(--foreground)] text-[11px] font-semibold text-[var(--background)]">
                You
              </span>
            </div>

            <div className="min-w-0 space-y-5">
                {run.isLoading ? (
                  <PixelLoader label="Connecting to the task transcript…" live />
                ) : run.isError || !run.data ? (
                  <div className="rounded-[14px] border border-[var(--phase-failed)]/35 bg-[var(--phase-failed-soft)] p-4 text-sm text-[var(--phase-failed-fg)]">
                    This task could not be loaded. It may have been removed.
                  </div>
                ) : (
                  <>
                    <PixelLoader
                      key={runId}
                      label={activityLabel(run.data, stream.events)}
                      live={isLive}
                      outcome={["failed", "cancelled", "interrupted"].includes(run.data.status) ? "stopped" : "complete"}
                      variant={run.data.phase === "qa" ? "Dots" : run.data.phase === "generate" ? "Orbit" : "Drive"}
                    />
                    <ThinkingPanel events={stream.events} summary={stream.summary} live={isLive} />
                    <ToolChipList events={stream.events} />
                    <StreamedAgentText events={stream.events} live={isLive} />
                    <AgentAssetStrip artifacts={artifacts.data} live={isLive} runId={runId} />

                    {run.data.news.source_url && (
                      <a href={run.data.news.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs text-[var(--link)] hover:underline">
                        Original story <ExternalLink className="size-3" />
                      </a>
                    )}

                    {run.data.pending_review && (
                      <div className="flex flex-wrap items-center gap-3 rounded-[14px] border border-[var(--border)] bg-[var(--card)] p-4">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">Your carousel is ready</p>
                          <p className="mt-1 text-xs leading-5 text-[var(--muted-foreground)]">
                            Review the cover and {Math.max(0, run.data.slide_count - 1)} remaining slides before anything is published.
                          </p>
                        </div>
                        <Link to={`/tasks/${runId}?tab=review`} className="rounded-[10px] bg-[var(--brand)] px-3 py-2 text-xs font-semibold text-[var(--brand-foreground)] hover:bg-[var(--brand-hover)]">
                          Review carousel
                        </Link>
                      </div>
                    )}
                  </>
                )}
            </div>
          </div>
        </div>

        <div className="agent-running-composer-dock">
          <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-8">
            <AgentComposer
              value=""
              onChange={() => undefined}
              onSubmit={() => undefined}
              onStop={isLive ? () => cancel.mutate() : undefined}
              onReset={reset}
              state={composerState}
            />
          </div>
        </div>
      </section>

      <AgentAssetRail artifacts={artifacts.data} loading={artifacts.isLoading} live={isLive} runId={runId} />
    </div>
  )
}
