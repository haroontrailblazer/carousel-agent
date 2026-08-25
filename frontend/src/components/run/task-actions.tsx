import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router"
import { RotateCcw, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { DeleteTaskDialog } from "@/components/run/delete-task-dialog"
import { Button } from "@/components/ui/button"
import { ApiError, del, post } from "@/lib/api"
import type { RunStatus } from "@/lib/types"

/**
 * Resume / re-run / delete for a task that is not currently running.
 *
 * Which actions appear is driven by status rather than shown-and-disabled:
 *
 *   interrupted         -> Resume and Delete
 *   failed / cancelled  -> Re-run and Delete
 *   anything else       -> nothing
 *
 * Resume and re-run are genuinely different and both earn their place. Resume
 * continues a task whose earlier phases succeeded, so the expensive generate
 * work is not repeated. Re-run starts clean from the same story, which is what
 * you want when the task failed rather than merely stopped.
 *
 * One component, used in the task list, the trace page and the review page -
 * three places where "this task is stuck, do something about it" is the
 * obvious next thought, and where three separate implementations would
 * eventually disagree about what delete removes or when it is allowed.
 */
export function TaskActions({
  runId,
  status,
  title,
  size = "sm",
  onDeleted,
}: {
  runId: string
  status: RunStatus
  title?: string | null
  size?: "sm" | "default"
  /** Where to go once the task no longer exists. Defaults to the task list. */
  onDeleted?: () => void
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [confirming, setConfirming] = React.useState(false)

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["runs"] })
    void queryClient.invalidateQueries({ queryKey: ["queue"] })
  }

  const fail = (error: unknown, fallback: string) => {
    const code = error instanceof ApiError ? error.code : undefined
    if (code === "too_many_active_runs") {
      toast.error("One at a time", {
        description: "A carousel is already being made. Wait for it to reach review.",
      })
      return
    }
    if (code === "nothing_to_rerun") {
      toast.error("Nothing to re-run", {
        description: "The original input for this task is gone.",
      })
      return
    }
    if (code === "run_not_finished" || code === "run_is_active") {
      toast.error("Still running", {
        description: "Only finished tasks can be deleted.",
      })
      return
    }
    toast.error(error instanceof Error ? error.message : fallback)
  }

  const resume = useMutation({
    mutationFn: () => post(`/api/runs/${runId}/resume`),
    onSuccess: () => {
      toast.success("Resuming", { description: "Picking up where it stopped." })
      refresh()
      void queryClient.invalidateQueries({ queryKey: ["run", runId] })
      void queryClient.invalidateQueries({ queryKey: ["trace", runId] })
      navigate(`/tasks/${runId}`)
    },
    onError: (e) => fail(e, "Could not resume that task."),
  })

  const rerun = useMutation({
    mutationFn: () => post<{ run_id: string }>(`/api/runs/${runId}/rerun`),
    onSuccess: (data) => {
      toast.success("Your carousel is cooking", { description: title ?? undefined })
      refresh()
      navigate(`/tasks/${data.run_id}`)
    },
    onError: (e) => fail(e, "Could not re-run that task."),
  })

  const remove = useMutation({
    mutationFn: () => del(`/api/runs/${runId}`),
    onSuccess: () => {
      setConfirming(false)
      toast.success("Task deleted")
      refresh()
      // The task is gone, so anything showing it must stop.
      if (onDeleted) onDeleted()
      else navigate("/tasks")
    },
    onError: (e) => fail(e, "Could not delete that task."),
  })

  const canResume = status === "interrupted"
  const canRerun = status === "failed" || status === "cancelled"
  const canDelete = canResume || canRerun || status === "done"
  if (!canResume && !canRerun && !canDelete) return null

  const busy = resume.isPending || rerun.isPending || remove.isPending
  const stop = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  return (
    <span className="flex items-center gap-1" onClick={stop}>
      {canResume && (
        <Button
          variant="default"
          size={size}
          disabled={busy}
          title="Resume from the phase it stopped in"
          onClick={(e) => {
            stop(e)
            resume.mutate()
          }}
        >
          <RotateCcw /> {resume.isPending ? "Resuming…" : "Resume"}
        </Button>
      )}

      {canRerun && (
        <Button
          variant="default"
          size={size}
          disabled={busy}
          title="Start a new task from the same story"
          onClick={(e) => {
            stop(e)
            rerun.mutate()
          }}
        >
          <RotateCcw /> {rerun.isPending ? "Starting…" : "Re-run"}
        </Button>
      )}

      {canDelete && (
        <>
          <Button
            variant="ghost"
            size="icon"
            disabled={busy}
            title="Delete this task, its trace and its media"
            onClick={(e) => {
              stop(e)
              setConfirming(true)
            }}
          >
            <Trash2 className="text-[var(--destructive)]" />
            <span className="sr-only">Delete task</span>
          </Button>

          <DeleteTaskDialog
            open={confirming}
            onOpenChange={setConfirming}
            runId={runId}
            title={title}
            busy={remove.isPending}
            onConfirm={() => remove.mutate()}
          />
        </>
      )}
    </span>
  )
}
