import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import { ArrowUpRight, Layers, Newspaper, Wrench, ShieldCheck } from "lucide-react"
import { toast } from "sonner"

import { AgentComposer, type ComposerState } from "@/components/agent/agent-composer"
import { AgentWorkspace } from "@/components/agent/agent-workspace"
import { StudioArtwork } from "@/components/layout/studio-artwork"
import { useRunWorkspace } from "@/hooks/use-run-workspace"
import { ApiError, get, post } from "@/lib/api"
import { designPayload, useCarouselDesigns } from "@/lib/designs"
import type { InstagramAccountSummary, Meta } from "@/lib/types"

const SUGGESTIONS = [
  "the new viral news in AI",
  "this week's biggest AI product launch",
  "a developer tool that just shipped something notable",
]

function looksLikeUrl(value: string): boolean {
  return /^https?:\/\/\S+$/i.test(value.trim())
}

/**
 * Which account this carousel is for.
 *
 * Shown BEFORE the run starts because the choice is not merely where the post
 * goes: the account's handle and profile picture are composited into every
 * slide as it is generated. Choosing afterwards would mean re-rendering the
 * carousel or shipping one brand's artwork under another's name.
 *
 * Every carousel pauses for review before sending or publishing.
 */
function AccountPicker({
  accounts,
  value,
  onChange,
  disabled,
}: {
  accounts: InstagramAccountSummary[]
  value: string
  onChange: (accountId: string) => void
  disabled: boolean
}) {
  const usable = accounts.filter((account) => !account.needs_reconnect)
  if (!usable.length) return null

  return (
    <div
      className="mt-4 flex flex-wrap items-center justify-center gap-2"
      aria-label="Instagram account"
    >
      <span className="text-[11px] text-[var(--muted-foreground)]">
        Instagram
      </span>
      {usable.map((account) => {
        const selected = account.id === value
        return (
          <button
            key={account.id}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => onChange(account.id)}
            className={
              "rounded-[10px] border px-2.5 py-1.5 text-xs transition-colors disabled:cursor-default disabled:opacity-50 " +
              (selected
                ? "border-[var(--foreground)] bg-[var(--muted)] text-[var(--foreground)]"
                : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:bg-[var(--muted)]")
            }
          >
            {account.handle}
          </button>
        )
      })}
    </div>
  )
}

/**
 * The New carousel screen.
 *
 * Two states in one route. With no `?run=`, it is a composer and a question.
 * With one, it hands over to `AgentWorkspace` - the same component the task
 * page's Chat tab renders, reading the same cache entries through the same
 * hook, so the two screens cannot disagree about a task.
 *
 * Several carousels can be in flight at once, so starting another is not
 * abandoning this one: `reset()` clears the URL and leaves the run working in
 * the background, exactly where Tasks will show it.
 */
export function NewRunRoute() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const runId = params.get("run")
  const [value, setValue] = React.useState("")
  const [submittedPrompt, setSubmittedPrompt] = React.useState("")
  const isUrl = looksLikeUrl(value)

  const workspace = useRunWorkspace(runId)

  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/api/meta") })
  const accounts = React.useMemo(
    () => meta.data?.accounts ?? [],
    [meta.data?.accounts],
  )
  // Omitted chooses the server default; no connected account still allows generation.
  const [accountId, setAccountId] = React.useState<string | undefined>(undefined)
  const [designs] = useCarouselDesigns()
  const [designId, setDesignId] = React.useState(() => designs.length === 1 ? designs[0].id : "")

  React.useEffect(() => {
    if (designId && designs.some((design) => design.id === designId)) return
    setDesignId(designs.length === 1 ? designs[0].id : "")
  }, [designId, designs])

  const start = useMutation({
    mutationFn: (payload: {
      source: string
      topic?: string
      url?: string
      account_id?: string
      design_id?: string
      design?: ReturnType<typeof designPayload>
    }) => post<{ run_id: string; title: string }>("/api/runs", payload),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      setSubmittedPrompt(value.trim())
      setParams({ run: data.run_id }, { replace: true })
      toast.success("Your carousel is cooking", { description: data.title })
    },
    onError: (error) => {
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "too_many_active_runs") {
        toast.error("Too many at once", {
          description:
            "Every slot is busy. Open Tasks to watch one, or wait for one to reach review.",
        })
        return
      }
      if (code === "daily_limit_reached") {
        toast.error("Daily limit reached", {
          description: "Raise MAX_RUNS_PER_DAY to allow more today.",
        })
        return
      }
      if (code === "no_account" || code === "account_needs_reconnect") {
        toast.error("No Instagram account", {
          description:
            "Reconnect the selected account from Profile → Instagram.",
        })
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not start that carousel.")
    },
  })

  function submit() {
    const trimmed = value.trim()
    if (trimmed.length < 3) return
    const design = designs.find((item) => item.id === designId)
    if (!design) {
      toast.error("Choose a design first", {
        description: "The agents need the cover and slide format before they start.",
      })
      return
    }
    start.mutate({
      ...(isUrl ? { source: "url", url: trimmed } : { source: "topic", topic: trimmed }),
      account_id: accountId,
      design_id: design.id,
      design: designPayload(design),
    })
  }

  /** Back to an empty composer. The run keeps working; Tasks still has it. */
  function reset() {
    setValue("")
    setSubmittedPrompt("")
    setParams({}, { replace: true })
  }

  if (runId) {
    return (
      <AgentWorkspace
        runId={runId}
        workspace={workspace}
        prompt={submittedPrompt}
        // The mutation keeps its result, so this is true only for the run
        // this tab actually created - not for the next chat opened from the
        // sidebar, which would otherwise inherit a stale "just started".
        justStarted={start.data?.run_id === runId}
        onReset={reset}
      />
    )
  }

  // No run yet, so there are only two things this composer can be: waiting for
  // you to type, or waiting for the server to hand back a run id.
  const composerState: ComposerState = start.isPending ? "starting" : "idle"

  return (
    <div className="agent-empty-workspace">
      <div className="studio-create">
        <div className="w-full">
          <div className="studio-intro studio-intro--illustrated">
            <div className="studio-intro-copy">
              <p className="studio-eyebrow">Ideas in. Carousels out.</p>
              <h1>A good story.<br /><span>A great carousel.</span></h1>
              <p className="studio-description">Your idea, a team of agents, and a little creative direction. Let’s make something worth swiping.</p>
            </div>
            <div className="studio-hero-art" aria-hidden="true">
              <StudioArtwork name="carousel-sculpture" priority sizes="(max-width: 767px) 120px, (max-width: 1100px) 210px, 300px" />
            </div>
          </div>

          {/* Design selection intentionally lives only in the slash-command menu. */}
          <AgentComposer
            value={value}
            onChange={setValue}
            onSubmit={submit}
            state={composerState}
            designs={designs}
            selectedDesignId={designId}
            onDesignChange={(nextDesignId) => {
              setDesignId(nextDesignId ?? "")
              const selected = designs.find((design) => design.id === nextDesignId)
              if (selected) toast.success("Design selected", { description: selected.name })
            }}
          />

          <p className="studio-prompt-label">Need a starting point?</p>
          <div className="studio-suggestions" aria-label="Suggested prompts">
            {SUGGESTIONS.map((suggestion, index) => (
              <button
                key={suggestion}
                type="button"
                disabled={start.isPending}
                onClick={() => setValue(suggestion)}
                className="studio-suggestion"
              >
                {index === 0 ? <Newspaper size={17} /> : index === 1 ? <Layers size={17} /> : <Wrench size={17} />}
                <span>{["Explore AI news", "Cover a product launch", "Spotlight a new tool"][index]}</span>
                <ArrowUpRight size={14} className="studio-suggestion-arrow" />
              </button>
            ))}
          </div>

          <AccountPicker
            accounts={accounts}
            value={accountId ?? (accounts.find((a) => a.is_default && !a.needs_reconnect)?.id ?? "")}
            onChange={setAccountId}
            disabled={start.isPending}
          />

          <p className="studio-review-note"><ShieldCheck size={14} /> You have the final say. Every carousel waits for your review.</p>
        </div>
      </div>
    </div>
  )
}
