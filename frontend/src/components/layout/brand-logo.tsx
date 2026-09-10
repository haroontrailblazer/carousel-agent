import { cn } from "@/lib/utils"

/** The creation hero sculpture is also our app mark. Adjacent text names it. */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <img
      src="/illustrations/carousel-sculpture-160.webp"
      alt=""
      aria-hidden="true"
      className={cn(
        "shrink-0 object-contain",
        className,
      )}
      width={64}
      height={64}
      draggable={false}
      // It is on screen on the first frame of every route, including sign-in,
      // so it should not queue behind anything the browser guesses is more
      // urgent. Decoding stays off the main thread.
      fetchPriority="high"
      decoding="async"
    />
  )
}
