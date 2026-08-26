import * as React from "react"
import { ArrowUp, Plus, RotateCcw, Square } from "lucide-react"

import { cn } from "@/lib/utils"

export type ComposerState = "idle" | "starting" | "running" | "complete" | "failed"

export function AgentComposer({
  value,
  onChange,
  onSubmit,
  onStop,
  onReset,
  state,
  stopping = false,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  onStop?: () => void
  onReset?: () => void
  state: ComposerState
  /** A stop is in flight; the button says so instead of looking ignored. */
  stopping?: boolean
}) {
  const inputRef = React.useRef<HTMLTextAreaElement>(null)
  const editable = state === "idle"

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (editable && value.trim().length >= 3) onSubmit()
  }

  const placeholder =
    state === "running" || state === "starting"
      ? "The carousel agent is working in the background…"
      : state === "complete"
        ? "Start another carousel whenever you are ready."
        : state === "failed"
          ? "This task stopped before it finished."
          : "Describe a story or paste a news URL…"

  return (
    <form
      onSubmit={submit}
      className="agent-composer rounded-[20px] border border-[var(--border)] bg-[var(--card)] p-2 shadow-[var(--shadow-lift)]"
    >
      <textarea
        ref={inputRef}
        rows={2}
        value={value}
        disabled={!editable}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault()
            submit(event)
          }
        }}
        placeholder={placeholder}
        className="max-h-40 min-h-14 w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 outline-none placeholder:text-[var(--muted-foreground)] disabled:cursor-default disabled:opacity-80"
        aria-label="Carousel prompt"
      />

      <div className="flex items-center justify-between px-1 pb-1">
        <button
          type="button"
          disabled={!editable}
          onClick={() => {
            if (!value) onChange("https://")
            requestAnimationFrame(() => inputRef.current?.focus())
          }}
          className="grid size-10 shrink-0 place-items-center rounded-full border border-[var(--border)] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] disabled:opacity-40"
          title="Paste a news URL"
        >
          <Plus className="size-4" />
          <span className="sr-only">Paste a news URL</span>
        </button>

        {state === "running" || state === "starting" ? (
          // Stop is live from the first frame, including while the run is
          // still "starting". The agents are already spending money by then,
          // and a button that refuses for the first few seconds is refusing at
          // exactly the moment someone realises they typed the wrong thing.
          <button
            type="button"
            onClick={onStop}
            disabled={!onStop || stopping}
            aria-busy={stopping}
            className="grid size-10 shrink-0 place-items-center rounded-full bg-[var(--foreground)] text-[var(--background)] transition-opacity hover:opacity-85 disabled:opacity-45"
            title={stopping ? "Stopping…" : "Stop the agents now"}
          >
            <Square className="size-3.5 fill-current" />
            <span className="sr-only">{stopping ? "Stopping" : "Stop task"}</span>
          </button>
        ) : state === "complete" || state === "failed" ? (
          <button
            type="button"
            onClick={onReset}
            className="grid size-10 shrink-0 place-items-center rounded-full bg-[var(--foreground)] text-[var(--background)] transition-opacity hover:opacity-85"
            title="Start another carousel"
          >
            <RotateCcw className="size-4" />
            <span className="sr-only">Start another carousel</span>
          </button>
        ) : (
          <button
            type="submit"
            disabled={value.trim().length < 3}
            className={cn(
              "grid size-10 shrink-0 place-items-center rounded-full bg-[var(--foreground)] text-[var(--background)] transition-all hover:-translate-y-px hover:opacity-85 disabled:translate-y-0 disabled:opacity-25",
            )}
            title="Generate carousel"
          >
            <ArrowUp className="size-4 stroke-[2.5]" />
            <span className="sr-only">Generate carousel</span>
          </button>
        )}
      </div>
    </form>
  )
}
