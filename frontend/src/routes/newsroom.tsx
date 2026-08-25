import * as React from "react"
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router"
import { ExternalLink, Newspaper, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { MutedChip } from "@/components/ui/chip"
import { ApiError, get, post } from "@/lib/api"
import { relativeTime } from "@/lib/format"
import type { QueueItem } from "@/lib/types"

/**
 * The Newsroom: stories the scheduler has fetched, waiting for someone to
 * choose one.
 *
 * Split out from the composer deliberately. Those are two different jobs -
 * "I have an idea" and "show me what came in" - and mixing them made the
 * composer page a wall of headlines you had to scroll past to reach the one
 * box you actually wanted.
 *
 * Picking a story does NOT navigate anywhere. You are browsing a list; being
 * yanked onto a task page after one click makes it hard to queue up a second,
 * and hides the list you were reading. The toast is the confirmation instead.
 */
export function NewsroomRoute() {
  const queryClient = useQueryClient()
  const [claiming, setClaiming] = React.useState<string | null>(null)

  const queue = useQuery({
    queryKey: ["queue"],
    queryFn: () => get<{ items: QueueItem[] }>("/api/queue"),
    // Shared cache entry with the sidebar badge, and never blanks on refetch.
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 120_000,
  })

  const fetchNow = useMutation({
    mutationFn: () => post<{ status: string }>("/api/schedule/run-now"),
    onSuccess: (result) => {
      if (result?.status === "already_running") {
        toast.info("Already checking", { description: "A feed check is in progress." })
        return
      }
      toast.success("Checking your feeds", {
        description: "New stories will appear here as they arrive.",
      })
      // The server answers immediately and keeps working; polling a few times
      // is what makes new arrivals show up without the user reloading.
      ;[8000, 20000, 45000, 90000].forEach((delay) =>
        window.setTimeout(
          () => void queryClient.invalidateQueries({ queryKey: ["queue"] }),
          delay,
        ),
      )
    },
    onError: () => toast.error("Could not check the feeds right now."),
  })

  const start = useMutation({
    mutationFn: (newsId: string) =>
      post<{ run_id: string; title: string }>("/api/runs", {
        source: "queue",
        news_id: newsId,
      }),
    onMutate: (newsId) => setClaiming(newsId),
    onSettled: () => setClaiming(null),
    onSuccess: (data) => {
      // Both lists change: the story leaves the queue, and a task appears.
      void queryClient.invalidateQueries({ queryKey: ["queue"] })
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
      toast.success("Your carousel is cooking", {
        description: data.title,
        duration: 6000,
        action: {
          label: "Watch it",
          onClick: () => {
            window.location.href = `/tasks/${data.run_id}`
          },
        },
      })
    },
    onError: (error) => {
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
      if (code === "queue_item_gone") {
        void queryClient.invalidateQueries({ queryKey: ["queue"] })
        toast.info("Already taken", {
          description: "Someone picked that story first.",
        })
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not start that.")
    },
  })

  const items = queue.data?.items ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Newsroom</h1>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Stories fetched from your feeds, waiting to be made into carousels.
          </p>
        </div>
        <Button
          variant="default"
          size="sm"
          onClick={() => fetchNow.mutate()}
          disabled={fetchNow.isPending}
        >
          <RefreshCw className={fetchNow.isPending ? "animate-spin-slow" : undefined} />
          {fetchNow.isPending ? "Checking…" : "Check feeds"}
        </Button>
      </div>

      {queue.isLoading && (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-24 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--muted)]"
            />
          ))}
        </div>
      )}

      {!queue.isLoading && items.length === 0 && (
        <Card className="p-10 text-center">
          <Newspaper className="mx-auto size-6 text-[var(--muted-foreground)]" />
          <p className="mt-3 font-medium">The newsroom is empty</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Stories arrive automatically from your RSS feeds every hour. You can
            also check right now, or write your own topic.
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button size="sm" onClick={() => fetchNow.mutate()} disabled={fetchNow.isPending}>
              Check feeds
            </Button>
            <Button variant="brand" size="sm" asChild>
              <Link to="/new">Write a topic</Link>
            </Button>
          </div>
        </Card>
      )}

      <div className="space-y-2">
        {items.map((item) => {
          const busy = claiming === item.id
          return (
            <Card key={item.id} className="p-4">
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-1">
                  <p className="font-medium leading-snug">{item.title}</p>
                  {item.summary && (
                    <p className="mt-1 line-clamp-2 text-sm text-[var(--muted-foreground)]">
                      {item.summary}
                    </p>
                  )}
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <MutedChip>{item.source_name || "unknown source"}</MutedChip>
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {relativeTime(item.created_at)}
                    </span>
                    {item.source_url && (
                      <a
                        href={item.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-[var(--link)] hover:underline"
                      >
                        read it <ExternalLink className="size-3" />
                      </a>
                    )}
                  </div>
                </div>

                <Button
                  variant="brand"
                  size="sm"
                  onClick={() => start.mutate(item.id)}
                  disabled={busy || start.isPending}
                >
                  {busy ? "Sending…" : "Use this"}
                </Button>
              </div>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
