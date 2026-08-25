import * as React from "react"

export type LoadingVariant = "Drive" | "Dots" | "Orbit" | "Surfer"

const chevron = Array.from({ length: 9 }, (_, index) => {
  const row = Math.floor(index / 3)
  const column = index % 3
  return (column + Math.abs(row - 1)) * 90
})

const ORBIT_ORDER = [0, 1, 2, 5, 8, 7, 6, 3]
const orbit = Array.from({ length: 9 }, (_, index) => {
  const position = ORBIT_ORDER.indexOf(index)
  return position === -1 ? null : position * 110
})

const PATTERNS: Record<Exclude<LoadingVariant, "Surfer">, {
  delays: (number | null)[]
  duration: number
  round: boolean
}> = {
  Drive: { delays: chevron, duration: 650, round: false },
  Dots: { delays: chevron, duration: 650, round: true },
  Orbit: { delays: orbit, duration: 950, round: false },
}

function LoaderGrid({
  delays,
  duration,
  round,
  running,
}: {
  delays: (number | null)[]
  duration: number
  round: boolean
  running: boolean
}) {
  return (
    <span aria-hidden className="agent-loading-grid">
      {delays.map((delay, index) => (
        <span
          key={index}
          className={round ? "rounded-full" : "rounded-[1px]"}
          style={{
            opacity: delay === null ? 0.07 : 0.15,
            animation: running && delay !== null
              ? `pixel-on ${duration}ms ease-in-out ${delay}ms infinite`
              : "none",
          }}
        />
      ))}
    </span>
  )
}

function useElapsed(running: boolean) {
  const [deciseconds, setDeciseconds] = React.useState(0)

  React.useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setDeciseconds((value) => value + 1), 100)
    return () => window.clearInterval(timer)
  }, [running])

  const total = deciseconds / 10
  if (total < 60) return `${total.toFixed(1)}s`
  return `${Math.floor(total / 60)}m ${(total % 60).toFixed(1)}s`
}

export function LoadingState({
  label,
  variant = "Drive",
  videoSrc = "/subway-surfers.mp4",
  running = true,
  outcome = "complete",
}: {
  label?: string
  variant?: LoadingVariant
  videoSrc?: string
  running?: boolean
  outcome?: string
}) {
  const elapsed = useElapsed(running)
  const surfer = variant === "Surfer"
  const resolvedLabel = label ?? (surfer ? "Subway surfing" : "Churning")
  const [videoOk, setVideoOk] = React.useState(true)
  const pattern = surfer ? PATTERNS.Drive : PATTERNS[variant]

  const labelElement = (
    <span className={running ? "agent-loading-label" : "text-[13px] font-medium text-[var(--muted-foreground)]"}>
      {resolvedLabel}
    </span>
  )
  const elapsedElement = (
    <span className="font-mono text-[12px] tabular-nums text-[var(--muted-foreground)]">
      {running ? elapsed : outcome}
    </span>
  )

  if (surfer) {
    return (
      <div role="status" className="flex w-fit flex-col items-start">
        <div className="flex items-center gap-2.5">
          <LoaderGrid {...pattern} running={running} />
          {labelElement}
          {elapsedElement}
        </div>

        <div className="agent-loading-video mt-2 w-56 overflow-hidden rounded-[10px] shadow-[var(--shadow-lift)]">
          <div className="relative aspect-video w-full bg-[var(--foreground)]">
            {videoOk ? (
              <video
                src={videoSrc}
                autoPlay={running}
                muted
                loop
                playsInline
                onError={() => setVideoOk(false)}
                className="h-full w-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 text-[var(--background)]">
                <LoaderGrid {...PATTERNS.Drive} running={running} />
                <span className="px-3 text-center font-mono text-[10px] opacity-65">
                  Video unavailable
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div role="status" className="flex w-fit max-w-full items-center gap-2.5" aria-live="polite">
      <LoaderGrid {...pattern} running={running} />
      {labelElement}
      {elapsedElement}
    </div>
  )
}
