import { StudioArtwork, type StudioArtworkName } from "@/components/layout/studio-artwork"
import { cn } from "@/lib/utils"

/** Decorative material study; the adjacent label always supplies the meaning. */
export function StudioEmblem({ name = "carousel-sculpture", small = false, className }: {
  name?: StudioArtworkName
  small?: boolean
  className?: string
}) {
  return (
    <span className={cn("studio-emblem", small && "studio-emblem--small", className)} aria-hidden="true">
      <StudioArtwork name={name} sizes={small ? "40px" : "72px"} />
    </span>
  )
}

export function agentArtwork(author: string): StudioArtworkName {
  if (/research|planner|search/.test(author)) return "research-lens"
  if (/phrasing|cta|copy/.test(author)) return "design-stylus"
  return "carousel-sculpture"
}
