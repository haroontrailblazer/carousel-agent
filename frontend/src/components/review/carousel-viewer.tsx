import * as React from "react"
import { Copy, ImageOff, Play } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { MutedChip } from "@/components/ui/chip"
import { IG_CAPTION_LIMIT } from "@/lib/format"
import type { RunArtifacts, SignedArtifact } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * One 4:5 frame.
 *
 * `object-contain`, never `cover`. The reviewer is checking typography, safe
 * areas and the brand rail at 1080x1350 - cropping to fit would hide exactly
 * the defects they are here to catch.
 *
 * The error state is explicit rather than a broken-image glyph: signed URLs
 * expire, so "this link went stale, reload" is a real and recoverable state
 * that the UI has to be able to say out loud.
 */
function SlideFrame({
  artifact,
  alt,
  onExpired,
  className,
}: {
  artifact: SignedArtifact | null
  alt: string
  onExpired?: () => void
  className?: string
}) {
  const [failed, setFailed] = React.useState(false)
  const [loaded, setLoaded] = React.useState(false)

  React.useEffect(() => {
    setFailed(false)
    setLoaded(false)
  }, [artifact?.url])

  if (!artifact?.url || failed) {
    return (
      <div
        className={cn(
          "slide-frame grid place-items-center rounded-[var(--radius-md)] border border-[var(--border)] p-4 text-center",
          className,
        )}
      >
        <div className="space-y-2">
          <ImageOff className="mx-auto size-5 text-[var(--muted-foreground)]" />
          <p className="text-xs text-[var(--muted-foreground)]">
            {artifact?.error ? "Could not load this slide." : "Link expired."}
          </p>
          {onExpired && (
            <Button size="sm" variant="ghost" onClick={onExpired}>
              Refresh links
            </Button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={cn("relative", className)}>
      {!loaded && (
        <div className="slide-frame absolute inset-0 animate-pulse rounded-[var(--radius-md)] bg-[var(--muted)]" />
      )}
      <img
        src={artifact.url}
        alt={alt}
        loading="lazy"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className="slide-frame w-full rounded-[var(--radius-md)] border border-[var(--border)]"
      />
    </div>
  )
}

/** The cover: a 1080x1350 MP4, or a still when no clip could be sourced. */
function Cover({
  cover,
  onExpired,
}: {
  cover: RunArtifacts["cover"]
  onExpired?: () => void
}) {
  if (cover.is_still || !cover.video?.url) {
    return (
      <div className="space-y-2">
        <SlideFrame artifact={cover.poster} alt="Cover" onExpired={onExpired} />
        <p className="text-xs text-[var(--muted-foreground)]">
          No source clip was found for this story, so the cover is a still
          image rather than a video.
        </p>
      </div>
    )
  }

  return (
    <video
      // muted + playsInline are not optional: iOS Safari refuses to autoplay
      // or inline-play without them, and the poster is what the reviewer sees
      // first while the file loads.
      controls
      muted
      playsInline
      loop
      preload="metadata"
      poster={cover.poster?.url ?? undefined}
      className="slide-frame w-full rounded-[var(--radius-md)] border border-[var(--border)]"
    >
      <source src={cover.video.url} type="video/mp4" />
    </video>
  )
}

export function CarouselViewer({
  artifacts,
  onExpired,
}: {
  artifacts: RunArtifacts
  onExpired?: () => void
}) {
  const [index, setIndex] = React.useState(0)

  const frames = React.useMemo(
    () => [
      { key: "cover", label: "Cover" },
      ...artifacts.slides.map((s) => ({ key: `slide-${s.index}`, label: `Slide ${s.index}` })),
      { key: "cta", label: "CTA" },
    ],
    [artifacts.slides],
  )

  const captionLength = artifacts.caption.length
  const overLimit = captionLength > IG_CAPTION_LIMIT

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_1fr]">
      <div className="space-y-3">
        <div>
          {index === 0 && <Cover cover={artifacts.cover} onExpired={onExpired} />}
          {index > 0 && index <= artifacts.slides.length && (
            <SlideFrame
              artifact={artifacts.slides[index - 1]}
              alt={`Slide ${artifacts.slides[index - 1]?.index}`}
              onExpired={onExpired}
            />
          )}
          {index === frames.length - 1 && (
            <SlideFrame artifact={artifacts.cta} alt="Call to action" onExpired={onExpired} />
          )}
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-[var(--muted-foreground)]">
            {index + 1} / {frames.length} · {frames[index]?.label}
          </span>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
              disabled={index === 0}
            >
              Prev
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setIndex((i) => Math.min(frames.length - 1, i + 1))}
              disabled={index === frames.length - 1}
            >
              Next
            </Button>
          </div>
        </div>

        {/* Thumbnail rail - the fastest way to spot a bad slide in a set. */}
        <div className="flex gap-2 overflow-x-auto pb-1">
          {frames.map((frame, i) => {
            const art =
              i === 0
                ? artifacts.cover.poster
                : i === frames.length - 1
                  ? artifacts.cta
                  : artifacts.slides[i - 1]
            return (
              <button
                key={frame.key}
                type="button"
                onClick={() => setIndex(i)}
                className={cn(
                  "shrink-0 overflow-hidden rounded-md border-2 transition-colors",
                  i === index ? "border-[var(--brand)]" : "border-transparent",
                )}
                aria-label={frame.label}
              >
                {art?.url ? (
                  <img
                    src={art.url}
                    alt=""
                    className="h-14 w-[45px] object-cover"
                    loading="lazy"
                  />
                ) : (
                  <span className="grid h-14 w-[45px] place-items-center bg-[var(--muted)]">
                    {i === 0 ? <Play className="size-3" /> : null}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      <Card className="p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">Caption</h3>
          <div className="flex items-center gap-2">
            <MutedChip
              style={
                overLimit
                  ? {
                      background: "var(--phase-failed-soft)",
                      color: "var(--phase-failed-fg)",
                    }
                  : undefined
              }
            >
              {captionLength} / {IG_CAPTION_LIMIT}
            </MutedChip>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void navigator.clipboard.writeText(artifacts.caption)
                toast.success("Caption copied")
              }}
            >
              <Copy /> Copy
            </Button>
          </div>
        </div>
        <p className="whitespace-pre-wrap text-sm leading-relaxed">
          {artifacts.caption || (
            <span className="text-[var(--muted-foreground)]">No caption yet.</span>
          )}
        </p>
      </Card>
    </div>
  )
}
