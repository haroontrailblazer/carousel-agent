import { cn } from "@/lib/utils"

/** Stacked slides form a C. The adjacent wordmark supplies the accessible name. */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <img
      src="/logo.svg"
      alt=""
      aria-hidden="true"
      className={cn(
        "shrink-0 object-contain",
        className,
      )}
      width={64}
      height={64}
      // It is on screen on the first frame of every route, including sign-in,
      // so it should not queue behind anything the browser guesses is more
      // urgent. Decoding stays off the main thread.
      fetchPriority="high"
      decoding="async"
    />
  )
}
