import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Round avatar that falls back to an initial.
 *
 * Pass a changing `key` (e.g. `key={src}`) when the src can change at
 * runtime - otherwise React keeps the old <img> and its failed state, and a
 * newly-set picture stays showing the initial.
 */
export function UserAvatar({
  src,
  name,
  className,
}: {
  src?: string | null
  name?: string | null
  className?: string
}) {
  const [ok, setOk] = React.useState(true)
  const initial = (name?.trim()?.[0] ?? "?").toUpperCase()
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden rounded-full",
        "bg-[var(--muted)] font-semibold uppercase ring-1 ring-[var(--border)]",
        className,
      )}
    >
      {src && ok ? (
        <img
          src={src}
          alt=""
          className="size-full object-cover"
          onError={() => setOk(false)}
        />
      ) : (
        <span>{initial}</span>
      )}
    </span>
  )
}

/**
 * Gravatar URL for an email, or null.
 *
 * SHA-256 via SubtleCrypto - Gravatar's modern hash. `d=404` so a missing
 * Gravatar fails the image load and UserAvatar falls back to the initial,
 * rather than serving a generic silhouette that looks like a real picture.
 */
export async function gravatarUrl(
  email: string,
  size = 80,
): Promise<string | null> {
  try {
    const normalized = email.trim().toLowerCase()
    const bytes = new TextEncoder().encode(normalized)
    const digest = await crypto.subtle.digest("SHA-256", bytes)
    const hash = Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("")
    return `https://www.gravatar.com/avatar/${hash}?s=${size}&d=404`
  } catch {
    return null
  }
}
