import * as React from "react"
import { coverShadowPixels } from "@/lib/cover-shadow"

export function CoverShadow({ height, blur, curve }: { height: number; blur: number; curve: number }) {
  const canvas = React.useRef<HTMLCanvasElement>(null)
  React.useLayoutEffect(() => {
    const context = canvas.current?.getContext("2d")
    if (!context) return
    const mask = context.createImageData(540, 675)
    mask.data.set(coverShadowPixels(540, 675, height, blur, curve))
    context.putImageData(mask, 0, 0)
  }, [height, blur, curve])
  return <canvas ref={canvas} width={540} height={675} className="simple-slide-shadow" aria-hidden="true" />
}
