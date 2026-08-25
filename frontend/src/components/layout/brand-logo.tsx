import { cn } from "@/lib/utils"

/** Shared decorative brand mark. Nearby text supplies the accessible name. */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <img
      src="/logo.webp"
      alt=""
      aria-hidden="true"
      className={cn(
        "shrink-0 object-contain drop-shadow-[0_3px_5px_rgb(22_24_17_/_0.18)]",
        className,
      )}
      width={64}
      height={64}
    />
  )
}
