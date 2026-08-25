import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { ArrowRight, Link2, Sparkles } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Chip, MutedChip } from "@/components/ui/chip"
import { Textarea } from "@/components/ui/input"
import { ApiError, get, post } from "@/lib/api"
import { relativeTime } from "@/lib/format"
import type { QueueItem } from "@/lib/types"

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

export function NewRunRoute() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [value, setValue] = React.useState("")
  const isUrl = looksLikeUrl(value)

  const queue = useQuery({
    queryKey: ["queue"],
    queryFn: () => get<{ items: QueueItem[] }>("/api/queue"),
  })

  const start = useMutation({
    mutationFn: (payload: { source: string; topic?: string; url?: string; news_id?: string }) =>
      post<{ run_id: string }>("/api/runs", payload),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      void queryClient.invalidateQueries({ queryKey: ["queue"] })
      navigate(`/runs/${data.run_id}`)
    },
    onError: (error) => {
      // Branch on the machine-readable code, never the message - the message
      // is prose and is expected to be reworded.
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "too_many_active_runs") {
        toast.error("A run is already going", {
          description: "Wait for it to reach review before starting another.",
        })
        return
      }
      if (code === "daily_limit_reached") {
        toast.error("Daily limit reached", {
          description: "Raise MAX_RUNS_PER_DAY to allow more runs today.",
        })
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not start the run.")
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
    <div className="space-y-8">
      <section>
        <h1 className="text-xl font-semibold tracking-tight">Make a carousel</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Describe a topic and the agents will find the story, or paste an
          article URL to use a specific one.
        </p>

        <Card className="mt-4 p-4">
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
              rows={3}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="the new viral news in AI"
              className="text-base"
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
      </section>

      <section>
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-base font-semibold tracking-tight">In the queue</h2>
          <span className="text-xs text-[var(--muted-foreground)]">
            Fetched automatically. Pick one to turn into a carousel.
          </span>
        </div>

        {queue.isLoading && (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-16 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--muted)]"
              />
            ))}
          </div>
        )}

        {queue.data?.items.length === 0 && (
          <Card className="p-6 text-center text-sm text-[var(--muted-foreground)]">
            Nothing queued yet. The scheduler tops this up from your RSS feeds.
          </Card>
        )}

        <div className="space-y-2">
          {queue.data?.items.map((item) => (
            <Card key={item.id} glide className="p-4">
              <div className="flex items-start gap-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{item.title}</p>
                  <p className="mt-0.5 line-clamp-1 text-sm text-[var(--muted-foreground)]">
                    {item.summary}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <MutedChip>{item.source_name || "unknown source"}</MutedChip>
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {relativeTime(item.created_at)}
                    </span>
                  </div>
                </div>
                <Button
                  size="sm"
                  onClick={() =>
                    start.mutate({ source: "queue", news_id: item.id })
                  }
                  disabled={start.isPending}
                >
                  Use this
                </Button>
              </div>
            </Card>
          ))}
        </div>
      </section>
    </div>
  )
}
