import * as React from "react"
import { PenLine, Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { HookOption } from "@/lib/types"

const LEVER_LABELS: Record<string, string> = {
  curiosity_gap: "Curiosity gap",
  two_beat_contrast: "Two beats",
  reader_stake: "Your stake",
  number_with_meaning: "Number",
  winner_loser: "Winner / loser",
  consequence: "Consequence",
  question: "Question",
  user_title: "Your title",
}

/** The rework request that swaps only the cover's line (older covers only). */
export function hookSwapFeedback(text: string, highlight: string): string {
  const marked = highlight ? ` with the highlighted words "${highlight}"` : ""
  return (
    `[first visual] Cover title only: change the cover title to exactly "${text}"${marked}. ` +
    "Keep the cover picture, the body slides and the caption as they are."
  )
}

function Highlighted({ text, highlight }: { text: string; highlight: string }) {
  const at = highlight ? text.toUpperCase().indexOf(highlight.toUpperCase()) : -1
  if (at < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, at)}
      <span style={{ color: "var(--phase-done)" }}>{text.slice(at, at + highlight.length)}</span>
      {text.slice(at + highlight.length)}
    </>
  )
}

/**
 * Choose the cover's hook: one of the planner's drafts, or your own words.
 *
 * Twelve rounds of rules could not guess what this client approves, so the
 * reviewer decides. On a cover with a saved untitled picture (`canRetitle`)
 * the new hook is drawn on in seconds, with no model and no rework, and every
 * choice is remembered as an example for future carousels. An older cover
 * falls back to a cover-only rework, behind a confirm.
 */
export function HookOptions({
  options,
  currentTitle,
  currentHighlight,
  canRetitle,
  busy,
  onApply,
}: {
  options: HookOption[]
  currentTitle?: string
  currentHighlight?: string
  canRetitle: boolean
  busy: boolean
  onApply: (text: string, highlight: string, source: "picked" | "typed") => void
}) {
  const [armed, setArmed] = React.useState<string | null>(null)
  const [own, setOwn] = React.useState("")
  const [ownHighlight, setOwnHighlight] = React.useState("")
  const highlightMissing = !!ownHighlight.trim() && !own.toUpperCase().includes(ownHighlight.trim().toUpperCase())

  const use = (text: string, highlight: string, source: "picked" | "typed") => {
    // Instant swaps are free and reversible; a rework is not, so it confirms.
    if (!canRetitle && armed !== text) {
      setArmed(text)
      return
    }
    setArmed(null)
    onApply(text, highlight, source)
  }

  return (
    <div className="mt-4 space-y-3 border-t border-[var(--border)] pt-4">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <Sparkles className="size-4" /> Cover hook
      </p>
      {currentTitle && (
        <p className="rounded-[var(--radius-md)] bg-[var(--muted)] p-2.5 text-sm font-semibold uppercase leading-snug">
          <Highlighted text={currentTitle} highlight={currentHighlight ?? ""} />
        </p>
      )}
      <p className="text-xs text-[var(--muted-foreground)]">
        {canRetitle
          ? "Pick another or write your own. The cover updates in seconds, and your choice teaches future hooks."
          : "This cover is older: swapping its hook runs a short cover-only rework."}
      </p>

      {options.length > 0 && (
        <ul className="space-y-2">
          {options.map((option) => {
            const isArmed = armed === option.text
            return (
              <li key={option.text} className="rounded-[var(--radius-md)] border border-[var(--border)] p-2.5">
                <p className="text-sm font-semibold uppercase leading-snug">
                  <Highlighted text={option.text} highlight={option.highlight} />
                </p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className="text-xs text-[var(--muted-foreground)]">
                    {LEVER_LABELS[option.lever] ?? option.lever}
                  </span>
                  <div className="flex gap-1.5">
                    {isArmed && (
                      <Button size="sm" variant="ghost" onClick={() => setArmed(null)}>
                        Cancel
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant={isArmed ? "brand" : "secondary"}
                      disabled={busy}
                      onClick={() => use(option.text, option.highlight, "picked")}
                    >
                      {isArmed ? "Rework cover with this" : "Use this hook"}
                    </Button>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      <div className="space-y-2 rounded-[var(--radius-md)] border border-dashed border-[var(--border)] p-2.5">
        <p className="flex items-center gap-1.5 text-xs font-medium">
          <PenLine className="size-3.5" /> Write your own
        </p>
        <Input
          value={own}
          onChange={(e) => setOwn(e.target.value)}
          placeholder="e.g. 53 CHATGPT PHOTOS LEAKED. NOBODY HACKED IN."
          maxLength={120}
          aria-label="Your cover hook"
        />
        <Input
          value={ownHighlight}
          onChange={(e) => setOwnHighlight(e.target.value)}
          placeholder="Words to highlight (optional, must be in the hook)"
          maxLength={60}
          aria-label="Words to highlight"
        />
        {highlightMissing && (
          <p className="text-xs text-[var(--destructive)]">The highlighted words must appear in the hook.</p>
        )}
        <Button
          size="sm"
          variant={armed === own.trim() && !!own.trim() ? "brand" : "secondary"}
          className="w-full"
          disabled={busy || own.trim().length < 3 || highlightMissing}
          onClick={() => use(own.trim(), ownHighlight.trim(), "typed")}
        >
          {armed === own.trim() && !!own.trim() ? "Rework cover with this" : canRetitle ? "Put this on the cover" : "Use my hook"}
        </Button>
      </div>
    </div>
  )
}
