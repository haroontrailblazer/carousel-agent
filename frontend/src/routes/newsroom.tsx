import * as React from "react"
import { StudioEmblem } from "@/components/layout/studio-emblem"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router"
import { ArrowUpRight, Image as ImageIcon, Loader2, Newspaper, RefreshCw, Sparkles, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, del, post } from "@/lib/api"
import { relativeTime } from "@/lib/format"
import { isRemembered, queueQuery, rememberQueue } from "@/lib/queries"
import type { QueueItem, QueueResponse } from "@/lib/types"
import "./newsroom.css"

function sourceUrl(value?: string): string | undefined {
  try {
    const url = new URL(value || "")
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}

function StoryThumbnail({ item }: { item: QueueItem }) {
  const [failedUrl, setFailedUrl] = React.useState<string | null>(null)
  const url = sourceUrl(item.thumbnail_url)
  return <div className="news-story-image">
    {url && url !== failedUrl
      ? <img src={url} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailedUrl(url)} />
      : <div className="news-story-fallback">
          <img src="/illustrations/research-lens-320.webp" alt="" />
          <span><ImageIcon /> No preview available</span>
        </div>}
    <span className="news-story-source">{item.source_name || "News source"}</span>
  </div>
}

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
    // Options live in lib/queries.ts: this list is remembered between visits
    // so the page paints instantly instead of waiting on a round trip to a
    // database that is half a world away, and the sidebar can prefetch the
    // very same cache entry on hover.
    ...queueQuery(),
    // While a check is running, ask often enough that arriving stories show
    // up on their own. Idle, hourly fetches do not deserve a heartbeat.
    refetchInterval: (query) => (query.state.data?.fetching ? 5_000 : 120_000),
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
      // Immediately, so the sidebar dot starts glowing on the click rather
      // than on the next poll - the response is the only thing that knows a
      // check just started.
      void queryClient.invalidateQueries({ queryKey: ["queue"] })
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

  const remove = useMutation({
    mutationFn: (newsId: string) => del<{ result: string }>("/api/queue/" + encodeURIComponent(newsId)),
    onSuccess: async (_data, newsId) => {
      await queryClient.cancelQueries({ queryKey: ["queue"] })
      queryClient.setQueryData<QueueResponse>(["queue"], current => {
        if (!current) return current
        const next = { ...current, items: current.items.filter(item => item.id !== newsId) }
        rememberQueue(next)
        return next
      })
      void queryClient.invalidateQueries({ queryKey: ["queue"] })
      void queryClient.invalidateQueries({ queryKey: ["pulse"] })
      toast.success("Story deleted", { description: "It won't return on the next feed check." })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "queue_item_gone") {
        void queryClient.invalidateQueries({ queryKey: ["queue"] })
      }
      toast.error("Could not delete this story", {
        description: error instanceof Error ? error.message : "Please try again.",
      })
    },
  })

  const items = queue.data?.items ?? []

  return (
    <div className="newsroom space-y-5">
      <div className="studio-page-heading flex flex-wrap items-center justify-between gap-3">
        <StudioEmblem name="research-lens" />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <h1 className="text-xl font-semibold tracking-tight">Newsroom</h1>
            {isRemembered(queue) && (
              <span className="text-xs text-[var(--muted-foreground)]">
                refreshingâ€¦
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Fresh stories. Your next carousel starts here.
          </p>
        </div>
        <Button
          variant="default"
          size="sm"
          onClick={() => fetchNow.mutate()}
          disabled={fetchNow.isPending}
        >
          <RefreshCw className={fetchNow.isPending ? "animate-spin-slow" : undefined} />
          {fetchNow.isPending ? "Checkingâ€¦" : "Check feeds"}
        </Button>
      </div>

      {queue.isLoading && <div className="news-story-grid" role="status" aria-label="Loading stories">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-96" />)}
      </div>}
      {queue.isError && <Card className="p-5" role="alert">
        <p className="font-medium">Couldn't refresh your stories</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">Check your connection and try again.</p>
        <Button variant="secondary" size="sm" className="mt-3" onClick={() => void queue.refetch()}>Try again</Button>
      </Card>}

      {!queue.isLoading && !queue.isError && items.length === 0 && (
        <Card className="p-10 text-center">
          <Newspaper className="mx-auto size-6 text-[var(--muted-foreground)]" />
          <p className="mt-3 font-medium">The newsroom is empty</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Stories arrive automatically from your RSS feeds. You can
            also check right now, or write your own topic.
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button size="sm" onClick={() => fetchNow.mutate()} disabled={fetchNow.isPending}>
              Check feeds
            </Button>
            <Button variant="brand" size="sm" asChild>
              <Link to="/new" viewTransition>Write a topic</Link>
            </Button>
          </div>
        </Card>
      )}

      {items.length > 0 && <>
        <div className="newsroom-section-label"><span>Ready to create</span><span>{items.length} {items.length === 1 ? "story" : "stories"}</span></div>
        <div className="news-story-grid">
          {items.map(item => {
            const busy = claiming === item.id
            const deleting = remove.isPending && remove.variables === item.id
            const href = sourceUrl(item.source_url)
            return <article key={item.id} className="news-story-card" aria-label={item.title} aria-busy={busy || deleting}>
              <StoryThumbnail item={item} />
              <div className="news-story-body">
                <div className="news-story-meta"><span>{relativeTime(item.published_at || item.created_at)}</span><span>From your feeds</span></div>
                <h2 className="news-story-title">{href
                  ? <a href={href} target="_blank" rel="noreferrer" title="Read original article">{item.title}<ArrowUpRight aria-hidden="true" /></a>
                  : item.title}</h2>
                <p className="news-story-description">{item.summary || "Open the original story for the details, or turn this headline into your next carousel."}</p>
                <div className="news-story-actions">
                  <Button variant="brand" size="sm" onClick={() => start.mutate(item.id)} disabled={start.isPending || remove.isPending}>
                    {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
                    {busy ? "Creating…" : "Create carousel"}
                  </Button>
                  <Button variant="ghost" size="sm" className="news-story-delete" onClick={() => remove.mutate(item.id)} disabled={start.isPending || remove.isPending}>
                    {deleting ? <Loader2 className="animate-spin" /> : <Trash2 />}
                    {deleting ? "Deleting…" : "Delete"}
                  </Button>
                </div>
              </div>
            </article>
          })}
        </div>
      </>}
    </div>
  )
}
