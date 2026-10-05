import * as React from "react"
import { Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { HookOption } from "@/lib/types"
import { cn } from "@/lib/utils"

const LEVER_LABELS: Record<string, string> = {
  two_beat_contrast: "Two beats",
  reader_stake: "Your stake",
  number_with_meaning: "Number",
  winner_loser: "Winner / loser",
  consequence: "Consequence",
  question: "Question",
  user_title: "Your title",
}

/** The rework request that swaps only the cover's line. */
export function hookSwapFeedback(option: HookOption): string {
  const highlight = option.highlight ? ` with the highlighted words "${option.highlight}"` : ""
  return (
    `[first visual] Cover title only: change the cover title to exactly "${option.text}"${highlight}. ` +
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
 * The other hooks the planner drafted for this cover.
 *
 * The planner writes several candidates on different levers and picks one;
 * the rest used to be thrown away, so a reviewer who liked a different line
 * had to type it into a rework. Here it is one click and a confirm - two
 * steps, because using one starts a cover rework.
 */
export function HookOptions({
  options,
  currentTitle,
  disabled,
  onUse,
}: {
  options: HookOption[]
  currentTitle?: string
  disabled: boolean
  onUse: (option: HookOption) => void
}) {
  const [armed, setArmed] = React.useState<string | null>(null)
  if (!options.length) return null
  return (
    <div className="mt-4 space-y-2 border-t border-[var(--border)] pt-4">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <Sparkles className="size-4" /> Other cover hooks
      </p>
      {currentTitle && (
        <p className="text-xs text-[var(--muted-foreground)]">
          On the cover now: <span className="font-medium uppercase">{currentTitle}</span>
        </p>
      )}
      <ul className="space-y-2">
        {options.map((option) => {
          const isArmed = armed === option.text
          return (
            <li
              key={option.text}
              className="rounded-[var(--radius-md)] border border-[var(--border)] p-2.5"
            >
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
                    className={cn(!isArmed && "text-xs")}
                    disabled={disabled}
                    onClick={() => (isArmed ? onUse(option) : setArmed(option.text))}
                    title={isArmed ? undefined : "Rebuilds only the cover with this line"}
                  >
                    {isArmed ? "Rework cover with this" : "Use this hook"}
                  </Button>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
