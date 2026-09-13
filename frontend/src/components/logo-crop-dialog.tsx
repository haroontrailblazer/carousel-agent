import * as React from "react"
import { LogoBackground } from "./design-logo"
import { RotateCcw, ZoomIn, ZoomOut } from "lucide-react"
import { type CarouselDesign } from "@/lib/designs"
import { constrainLogoCrop, drawDesignLogo, INITIAL_LOGO_CROP, prepareDesignLogo, type LogoCrop } from "@/lib/design-logo"
import { Button } from "./ui/button"
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog"

function CropPreview({ image, crop, background, logoBackground, label }: {
  image: HTMLImageElement; crop: LogoCrop; background: string; logoBackground: string; label: string
}) {
  const canvas = React.useRef<HTMLCanvasElement>(null)
  React.useEffect(() => {
    if (canvas.current) drawDesignLogo(canvas.current, image, crop, 128, logoBackground)
  }, [image, crop, logoBackground])
  return <div className="logo-crop-example"><div style={{ background }}><canvas ref={canvas} role="img" aria-label={label + " logo preview"} /></div><span>{label}</span></div>
}

export function LogoCropDialog({ image, design, onApply, onClose, restoreFocus }: {
  image: HTMLImageElement
  design: CarouselDesign
  onApply: (dataUrl: string, background: string) => void
  onClose: () => void
  restoreFocus: () => void
}) {
  const [crop, setCrop] = React.useState(INITIAL_LOGO_CROP)
  const [logoBackground, setLogoBackground] = React.useState(design.logoBackground ?? "")
  const [error, setError] = React.useState("")
  const gesture = React.useRef<{ pointer: number; x: number; y: number; size: number; crop: LogoCrop } | null>(null)
  const instructions = React.useId()
  const zoomId = React.useId()

  // Radix mounts portal content after the parent. Paint when the canvas itself
  // mounts, so the initial crop is visible before the first drag or zoom.
  const paintCanvas = React.useCallback((canvas: HTMLCanvasElement | null) => {
    if (!canvas) return
    try { drawDesignLogo(canvas, image, crop, 512, logoBackground) }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not preview this image.") }
  }, [image, crop, logoBackground])

  function move(start: LogoCrop, dx: number, dy: number, size: number) {
    const edge = Math.min(image.naturalWidth, image.naturalHeight) / start.zoom
    setCrop(constrainLogoCrop(image, {
      ...start, x: start.x - dx / size * edge / image.naturalWidth,
      y: start.y - dy / size * edge / image.naturalHeight,
    }))
  }

  function apply() {
    try { onApply(prepareDesignLogo(image, crop), logoBackground) }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save this crop.") }
  }

  return <Dialog open onOpenChange={open => { if (!open) onClose() }}>
    <DialogContent className="logo-crop-dialog" onCloseAutoFocus={event => { event.preventDefault(); restoreFocus() }}>
      <DialogHeader><DialogTitle>Crop your logo</DialogTitle><DialogDescription>Choose what stays inside the circle.</DialogDescription></DialogHeader>
      <DialogBody className="logo-crop-body space-y-0">
        <div className="logo-crop-stage" tabIndex={0} role="group" aria-label="Position logo" aria-describedby={instructions}
          onPointerDown={event => {
            if (!event.isPrimary || event.button !== 0) return
            event.currentTarget.focus()
            event.currentTarget.setPointerCapture(event.pointerId)
            gesture.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, size: event.currentTarget.getBoundingClientRect().width, crop }
          }}
          onPointerMove={event => {
            const start = gesture.current
            if (start?.pointer === event.pointerId) move(start.crop, event.clientX - start.x, event.clientY - start.y, start.size)
          }}
          onLostPointerCapture={() => { gesture.current = null }}
          onPointerUp={event => { if (gesture.current?.pointer === event.pointerId) { gesture.current = null; event.currentTarget.releasePointerCapture(event.pointerId) } }}
          onPointerCancel={() => { gesture.current = null }}
          onKeyDown={event => {
            const step = event.shiftKey ? 10 : 2
            const deltas: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
            if (deltas[event.key]) { event.preventDefault(); move(crop, ...deltas[event.key], 100) }
          }}>
          <canvas ref={paintCanvas} aria-label="Your circular logo crop" role="img" />
        </div>
        <p className="logo-crop-hint" id={instructions}>Drag to position, or use the arrow keys.</p>
        <div className="logo-crop-zoom-label"><label htmlFor={zoomId}>Zoom</label><output htmlFor={zoomId}>{Math.round(crop.zoom * 100)}%</output></div>
        <div className="logo-crop-zoom"><ZoomOut aria-hidden="true" /><input id={zoomId} type="range" min="1" max="4" step="0.01" value={crop.zoom} aria-valuetext={Math.round(crop.zoom * 100) + "%"}
          onChange={event => setCrop(constrainLogoCrop(image, { ...crop, zoom: Number(event.target.value) }))} /><ZoomIn aria-hidden="true" /></div>
        <LogoBackground value={logoBackground} onChange={setLogoBackground} />
        <div className="logo-crop-preview-heading"><span>On your slides</span><button type="button" onClick={() => { setCrop(INITIAL_LOGO_CROP); setError("") }}><RotateCcw aria-hidden="true" />Reset</button></div>
        <div className="logo-crop-examples">
          <CropPreview image={image} crop={crop} logoBackground={logoBackground} background="#000000" label="Cover" />
          <CropPreview image={image} crop={crop} logoBackground={logoBackground} background={design.inside.background} label="Inside" />
          <CropPreview image={image} crop={crop} logoBackground={logoBackground} background={(design.cta ?? design.inside).background} label="CTA" />
        </div>
        <p className="logo-crop-hint">This crop is saved with your design and used in the generated slides.</p>
        {error && <p className="simple-branding-error" role="alert">{error}</p>}
      </DialogBody>
      <DialogFooter className="logo-crop-footer"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="button" variant="brand" onClick={apply}>Use this crop</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
