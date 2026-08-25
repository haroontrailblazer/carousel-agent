/**
 * Live run trace over Server-Sent Events.
 *
 * The principle that makes reload, reconnect and the Telegram race all fall
 * out for free: **the server owns the log, the client owns a projection.** The
 * stream never becomes the source of truth for the carousel itself - that
 * always comes from the REST snapshot.
 *
 * EventSource is used rather than a fetch reader specifically because it
 * reconnects on its own and replays Last-Event-ID, and because the session is
 * a cookie it can send. A fetch reader would need a manual backoff loop, and a
 * bearer token would have to go in the query string, which writes the
 * credential into every access log on the way.
 *
 * The server emits `id: <seq>` on every frame and replays anything after the
 * cursor, so a dropped connection loses nothing and duplicates nothing.
 */

import * as React from "react"

import type { RunEvent } from "@/lib/types"

export type TraceState = {
  events: RunEvent[]
  /** True once history has been replayed and the stream is live. */
  synced: boolean
  /** Latest phase seen on the stream, if any. */
  phase: string | null
  /** Set when the client was told it missed events and should refetch. */
  gapped: boolean
  connected: boolean
}

const EMPTY: TraceState = {
  events: [],
  synced: false,
  phase: null,
  gapped: false,
  connected: false,
}

/**
 * Subscribe to a run's timeline.
 *
 * @param runId    the run to watch, or null to watch nothing
 * @param onPhase  called when the phase changes, so the caller can refetch the
 *                 authoritative snapshot (bundle, pending_review, artifacts)
 * @param onEnd    called when the run reaches a terminal state
 */
export function useRunStream(
  runId: string | null,
  { onPhase, onEnd }: { onPhase?: (phase: string) => void; onEnd?: () => void } = {},
): TraceState {
  const [state, setState] = React.useState<TraceState>(EMPTY)

  // Kept in refs so changing callbacks never tears down the connection - a
  // reconnect on every parent render would hammer the server and reset the
  // trace.
  const onPhaseRef = React.useRef(onPhase)
  const onEndRef = React.useRef(onEnd)
  React.useEffect(() => {
    onPhaseRef.current = onPhase
    onEndRef.current = onEnd
  })

  React.useEffect(() => {
    if (!runId) {
      setState(EMPTY)
      return
    }
    setState(EMPTY)

    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`, {
      withCredentials: true,
    })

    source.onopen = () => {
      setState((s) => ({ ...s, connected: true }))
    }

    source.addEventListener("synced", () => {
      setState((s) => ({ ...s, synced: true, connected: true }))
    })

    source.addEventListener("run", (message) => {
      let event: RunEvent
      try {
        event = JSON.parse((message as MessageEvent).data)
      } catch {
        // Ignore one malformed frame rather than tearing down the stream over
        // it - the next frame is very likely fine.
        return
      }

      setState((s) => {
        // The server already dedupes against the replay cursor, but a
        // reconnect can overlap by one; keep it idempotent here too.
        if (s.events.some((e) => e.seq === event.seq && event.kind !== "gap")) {
          return s
        }
        const phase =
          typeof event.data?.phase === "string" ? (event.data.phase as string) : s.phase
        return {
          ...s,
          events: [...s.events, event],
          phase,
          gapped: s.gapped || event.kind === "gap",
          connected: true,
        }
      })

      const phase = event.data?.phase
      if (typeof phase === "string") onPhaseRef.current?.(phase)
      if (event.kind === "terminal") onEndRef.current?.()
    })

    source.onerror = () => {
      // EventSource retries on its own and replays Last-Event-ID; surface the
      // state so the UI can show "reconnecting" rather than pretending all is
      // well or, worse, showing an error the user cannot act on.
      setState((s) => ({ ...s, connected: false }))
    }

    return () => source.close()
  }, [runId])

  return state
}

/** Group a flat event list by the agent that produced it, preserving order. */
export function groupByAuthor(
  events: RunEvent[],
): { author: string; events: RunEvent[] }[] {
  const groups: { author: string; events: RunEvent[] }[] = []
  for (const event of events) {
    const author = event.author || "carousel_orchestrator"
    const last = groups[groups.length - 1]
    if (last && last.author === author) last.events.push(event)
    else groups.push({ author, events: [event] })
  }
  return groups
}
