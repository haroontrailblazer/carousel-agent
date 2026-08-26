import { cn } from "@/lib/utils"

/**
 * A placeholder shaped like the thing that is coming.
 *
 * The rule this exists to enforce: a skeleton must match the layout it stands
 * in for. The console had a `h-64` grey rectangle standing in for a task
 * detail page and a `h-96` one for a carousel, and both did the thing a
 * skeleton is supposed to prevent - the page settled, then jumped, because
 * what arrived was not the shape that was being held. A block of roughly the
 * right height in roughly the right place costs the same and stops the jump.
 *
 * `aria-hidden` throughout: a screen reader gets the real content when it
 * lands, and reading out a scaffold in the meantime is noise. The screens
 * announce their own waiting state in words where it matters.
 *
 * `motion-reduce:animate-none` rather than a media query in the stylesheet,
 * so the exception travels with the component instead of needing a matching
 * entry in a list somewhere else that a new skeleton would be left out of.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-pulse rounded-[var(--radius-md)] bg-[var(--muted)]",
        "motion-reduce:animate-none",
        className,
      )}
    />
  )
}

/**
 * The task-list placeholder, used both by the list itself and by anything
 * that needs to stand in for it while a screen loads.
 */
export function SkeletonRows({
  rows = 4,
  className,
}: {
  rows?: number
  className?: string
}) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton
          key={i}
          // Matches the real row: a Card at p-4 holding a title line and a
          // row of chips.
          className="h-20 rounded-[var(--radius)] border border-[var(--border)]"
        />
      ))}
    </div>
  )
}
