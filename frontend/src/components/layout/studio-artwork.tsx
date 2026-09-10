import { cn } from "@/lib/utils"

export type StudioArtworkName = "carousel-sculpture" | "research-lens" | "design-stylus"

/** Decorative product renders: adjacent headings and controls carry the meaning. */
export function StudioArtwork({ name, className, priority = false, sizes = "72px" }: {
  name: StudioArtworkName
  className?: string
  priority?: boolean
  sizes?: string
}) {
  const widths = name === "carousel-sculpture" ? [160, 320, 640] : [160, 320]
  return (
    <img
      src={`/illustrations/${name}-320.webp`}
      srcSet={widths.map(width => `/illustrations/${name}-${width}.webp ${width}w`).join(", ")}
      sizes={sizes}
      alt=""
      aria-hidden="true"
      width={640}
      height={640}
      loading={priority ? "eager" : "lazy"}
      fetchPriority={priority ? "high" : "low"}
      decoding="async"
      draggable={false}
      className={cn("studio-artwork", className)}
    />
  )
}
