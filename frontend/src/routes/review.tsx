import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useParams } from "react-router"
import { ArrowLeft } from "lucide-react"
import { toast } from "sonner"

import { ApprovalCard } from "@/components/review/approval-card"
import { CarouselViewer } from "@/components/review/carousel-viewer"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { useRunStream } from "@/hooks/use-run-stream"
import { ApiError, get, post } from "@/lib/api"
import type { Meta, RunArtifacts, RunDetail } from "@/lib/types"

export function ReviewRoute() {
  const { runId = "" } = useParams()
  const queryClient = useQueryClient()

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => get<RunDetail>(`/api/runs/${runId}`),
    // pending_review is the authoritative flag and it is NOT monotonic - a
    // failed resume restores the pending row. Poll it while this screen is
    // open so the card cannot get stuck.
    refetchInterval: 8_000,
  })

  const artifacts = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => get<RunArtifacts>(`/api/runs/${runId}/artifacts`),
    // Signed URLs expire. Refetching well inside their lifetime keeps a long
    // review session from turning into a wall of broken images.
    refetchInterval: 15 * 60_000,
  })

  const meta = useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/api/meta") })

  // Watch the stream so a decision made in Telegram flips this screen without
  // a reload - that is the whole point of both surfaces sharing one code path.
  useRunStream(runId, {
    onPhase: () => void queryClient.invalidateQueries({ queryKey: ["run", runId] }),
    onEnd: () => void queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  })

  const decide = useMutation({
    mutationFn: (payload: { status: string; feedback: string }) =>
      post(`/api/runs/${runId}/verdict`, payload),
    onSuccess: (_data, variables) => {
      toast.success(
        variables.status === "approved" ? "Approved — publishing" : "Rejected — reworking",
      )
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["runs"] })
    },
    onError: (error) => {
      // Branch on the code, never the message. 409 here means someone decided
      // it first - almost always the same person, from Telegram. That is a
      // normal outcome, so it is reported as information, not a failure.
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "not_pending") {
        toast.info("Already decided", {
          description: "This run was decided elsewhere — probably from Telegram.",
        })
        void queryClient.invalidateQueries({ queryKey: ["run", runId] })
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not record that.")
    },
  })

  if (run.isLoading) {
    return <div className="h-96 animate-pulse rounded-[var(--radius)] bg-[var(--muted)]" />
  }
  if (!run.data) {
    return (
      <Card className="p-6">
        <p className="font-medium">Run not found</p>
        <Button className="mt-4" variant="ghost" asChild>
          <Link to="/runs">Back to runs</Link>
        </Button>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/runs/${runId}`}>
            <ArrowLeft /> Trace
          </Link>
        </Button>
        <h1 className="truncate text-lg font-semibold tracking-tight">
          {run.data.title || run.data.news.title || runId}
        </h1>
      </div>

      <ApprovalCard
        run={run.data}
        publishConfigured={meta.data?.publish_configured ?? true}
        busy={decide.isPending}
        onApprove={() => decide.mutate({ status: "approved", feedback: "" })}
        onReject={(feedback) => decide.mutate({ status: "rejected", feedback })}
      />

      {artifacts.isLoading && (
        <div className="h-96 animate-pulse rounded-[var(--radius)] bg-[var(--muted)]" />
      )}
      {artifacts.isError && (
        <Card className="p-6 text-sm text-[var(--muted-foreground)]">
          This run has not assembled a carousel yet.
        </Card>
      )}
      {artifacts.data && (
        <CarouselViewer
          artifacts={artifacts.data}
          onExpired={() => void artifacts.refetch()}
        />
      )}
    </div>
  )
}
