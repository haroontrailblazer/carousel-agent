import * as React from "react"
import { preload } from "react-dom"
import { Check, Copy, ImageOff, Play } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { MutedChip } from "@/components/ui/chip"
import { IG_CAPTION_LIMIT } from "@/lib/format"
import type { CoverChoice, RunArtifacts, SignedArtifact } from "@/lib/types"
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
        // NOT lazy. Exactly one slide is mounted at a time - the one being
        // reviewed - so this is always the largest visible thing on the
        // screen, and `loading="lazy"` was telling the browser to deprioritise
        // the only image the page exists to show. The thumbnail rail below is
        // where lazy belongs, and still uses it.
        loading="eager"
        fetchPriority="high"
        decoding="async"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className="slide-frame w-full rounded-[var(--radius-md)] border border-[var(--border)]"
      />
    </div>
  )
}

/**
 * The cover, which may be a clip, a still, or a choice between the two.
 *
 * When the run produced both, the reviewer decides which one Instagram gets
 * as the first slide - and that decision is required, not defaulted. A silent
 * default would mean posting a video when someone wanted the still, and the
 * post is public before anyone notices.
 */
function Cover({
  cover,
  choice,
  onChoose,
  onExpired,
}: {
  cover: RunArtifacts["cover"]
  choice: CoverChoice
  onChoose: (choice: CoverChoice) => void
  onExpired?: () => void
}) {
  // Both are offered whenever both FILES exist.
  //
  // `is_still` does not mean "there is no video" - it means no source clip
  // could be found for the story, so the pipeline built a slow-zoom video from
  // the still instead. That mp4 is real and publishable, so gating the choice
  // on is_still hid a genuine option: measured on a live task reporting
  // is_still=true, both cover.mp4 and cover-poster.png were present.
  const hasVideo = !!cover.video?.url
  const hasImage = !!cover.poster?.url

  const video = (
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
      <source src={cover.video?.url ?? undefined} type="video/mp4" />
    </video>
  )

  if (!hasVideo) {
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

  if (!hasImage) return video

  // Both exist: present them as a deliberate either/or.
  const option = (
    key: Exclude<CoverChoice, null>,
    label: string,
    hint: string,
    preview: React.ReactNode,
  ) => {
    const picked = choice === key
    return (
      <button
        type="button"
        onClick={() => onChoose(key)}
        aria-pressed={picked}
        className={cn(
          "group rounded-[var(--radius-md)] border-2 p-1.5 text-left transition-colors",
          picked
            ? "border-[var(--brand)] bg-[var(--brand-soft)]"
            : "border-[var(--border)] hover:border-[var(--muted-foreground)]",
        )}
      >
        <span className="pointer-events-none block">{preview}</span>
        <span className="mt-1.5 flex items-center gap-1.5 px-1">
          <span
            aria-hidden
            className={cn(
              "grid size-3.5 shrink-0 place-items-center rounded-full border",
              picked
                ? "border-[var(--brand)] bg-[var(--brand)]"
                : "border-[var(--muted-foreground)]",
            )}
          >
            {picked && <Check className="size-2.5 text-[var(--brand-foreground)]" />}
          </span>
          <span className="text-xs font-medium">{label}</span>
        </span>
        <span className="mt-0.5 block px-1 text-[11px] text-[var(--muted-foreground)]">
          {hint}
        </span>
      </button>
    )
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        {option(
          "video",
          "Video cover",
          cover.is_still
            ? `${Math.round(cover.duration_s)}s slow zoom on the still`
            : `${Math.round(cover.duration_s)}s clip from the story`,
          <span className="pointer-events-auto block">{video}</span>,
        )}
        {option(
          "image",
          "Image cover",
          "Still frame",
          <SlideFrame artifact={cover.poster} alt="Cover still" onExpired={onExpired} />,
        )}
      </div>
      {!choice && (
        <p
          className="rounded-[var(--radius-md)] px-3 py-2 text-xs"
          style={{
            background: "var(--phase-review-soft)",
            color: "var(--phase-review-fg)",
          }}
        >
          This task has both a video and an image cover. Pick which one goes out
          as the first slide before approving.
        </p>
      )}
    </div>
  )
}

export function CarouselViewer({
  artifacts,
  coverChoice,
  onCoverChoice,
  onExpired,
}: {
  artifacts: RunArtifacts
  coverChoice: CoverChoice
  onCoverChoice: (choice: CoverChoice) => void
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

  // Fetch the slides either side of this one, so Prev and Next are instant.
  //
  // Reviewing a carousel means stepping through six or seven slides in a row,
  // and only one is mounted at a time - so before this, every step was a fresh
  // download of a full-resolution render while the reviewer looked at a grey
  // box. The neighbours are the two the user is overwhelmingly likely to ask
  // for next, and one of them is usually the one they just came from.
  //
  // React's `preload` emits a <link rel="preload"> and deduplicates by href,
  // so re-running this on every step costs nothing for slides already fetched,
  // and the browser treats it as lower priority than the image on screen.
  React.useEffect(() => {
    const urlAt = (i: number): string | null | undefined => {
      if (i < 0 || i >= frames.length) return undefined
      if (i === 0) return artifacts.cover.poster?.url
      if (i === frames.length - 1) return artifacts.cta?.url
      return artifacts.slides[i - 1]?.url
    }
    for (const neighbour of [index + 1, index - 1]) {
      const url = urlAt(neighbour)
      if (url) preload(url, { as: "image" })
    }
  }, [index, frames.length, artifacts])

  const captionLength = artifacts.caption.length
  const overLimit = captionLength > IG_CAPTION_LIMIT

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_1fr]">
      <div className="space-y-3">
        <div>
          {index === 0 && (
            <Cover
              cover={artifacts.cover}
              choice={coverChoice}
              onChoose={onCoverChoice}
              onExpired={onExpired}
            />
          )}
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
