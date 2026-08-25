import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { ArrowRight, Link2, Newspaper, Sparkles } from "lucide-react"
import { Link } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Chip } from "@/components/ui/chip"
import { Textarea } from "@/components/ui/input"
import { ApiError, post } from "@/lib/api"

const SUGGESTIONS = [
  "the new viral news in AI",
  "this week's biggest AI product launch",
  "a developer tool that just shipped something notable",
]

/** Looks like a URL the moment it starts with a scheme, so the chip can flip
 *  as the user pastes rather than after they submit. */
function looksLikeUrl(value: string): boolean {
  return /^https?:\/\/\S+$/i.test(value.trim())
}

/**
 * The composer, and nothing else.
 *
 * The fetched-story list used to live under this box and has moved to the
 * Newsroom. Two different jobs were sharing one page: "I have an idea" and
 * "show me what came in". Keeping the list here meant the one input you came
 * for sat above a wall of headlines.
 *
 * Unlike the Newsroom, submitting here DOES navigate to the new task - you
 * just described something specific, so watching it start is what you want
 * next.
 */
export function NewRunRoute() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [value, setValue] = React.useState("")
  const isUrl = looksLikeUrl(value)

  const start = useMutation({
    mutationFn: (payload: { source: string; topic?: string; url?: string }) =>
      post<{ run_id: string; title: string }>("/api/runs", payload),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      toast.success("Your carousel is cooking", { description: data.title })
      navigate(`/tasks/${data.run_id}`)
    },
    onError: (error) => {
      // Branch on the machine-readable code, never the message - the message
      // is prose and is expected to be reworded.
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "too_many_active_runs") {
        toast.error("One at a time", {
          description: "A carousel is already being made. Wait for it to reach review.",
        })
        return
      }
      if (code === "daily_limit_reached") {
        toast.error("Daily limit reached", {
          description: "Raise MAX_RUNS_PER_DAY to allow more today.",
        })
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not start that.")
    },
  })

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = value.trim()
    if (trimmed.length < 3) return
    start.mutate(
      isUrl ? { source: "url", url: trimmed } : { source: "topic", topic: trimmed },
    )
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Make a carousel</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Describe a topic and the agents will find the story, or paste an
          article URL to use a specific one.
        </p>
      </div>

      <Card className="p-4">
        <form onSubmit={submit}>
          <div className="mb-2 flex items-center gap-2">
            {isUrl ? (
              <Chip tone="qa" dot>
                <Link2 className="size-3" /> News URL
              </Chip>
            ) : (
              <Chip tone="generate" dot>
                <Sparkles className="size-3" /> Topic
              </Chip>
            )}
            <span className="text-xs text-[var(--muted-foreground)]">
              {isUrl
                ? "The article text and images will be pulled from this page."
                : "Research will search the web for the story behind this."}
            </span>
          </div>

          <Textarea
            rows={4}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="the new viral news in AI"
            className="text-base"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e)
            }}
          />

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {!value &&
              SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setValue(s)}
                  className="rounded-[var(--radius-pill)] border border-[var(--border)] px-3 py-1 text-xs text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                >
                  {s}
                </button>
              ))}
            <Button
              type="submit"
              variant="brand"
              className="ml-auto"
              disabled={value.trim().length < 3 || start.isPending}
            >
              {start.isPending ? "Starting…" : "Generate"}
              <ArrowRight />
            </Button>
          </div>
        </form>
      </Card>

      <p className="text-center text-sm text-[var(--muted-foreground)]">
        Or pick something that already came in —{" "}
        <Link to="/newsroom" className="inline-flex items-center gap-1 text-[var(--link)] hover:underline">
          <Newspaper className="size-3.5" /> the Newsroom
        </Link>
      </p>
    </div>
  )
}
