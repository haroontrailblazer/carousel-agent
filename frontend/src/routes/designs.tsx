import * as React from "react"
import { Link } from "react-router"
import { AlignCenter, AlignLeft, AlignRight, ArrowRight, Check, Copy, Eye, Image as ImageIcon, Lock, MousePointer2, PanelsTopLeft, Plus, RotateCcw, Trash2, Type, Undo2, Unlock } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { DesignBranding } from "@/components/design-branding"
import { DesignGenerationSettings } from "@/components/design-generation-settings"
import { StudioEmblem } from "@/components/layout/studio-emblem"
import { type CarouselDesign, type DesignImageType, type DesignPosition, type ElementTransform, duplicateDesign, newDesign, PREBUILT_DESIGNS, useCarouselDesigns } from "@/lib/designs"
import "./design-editor.css"

type Surface = "cover" | "inside" | "cta"
type ElementKind = "title" | "image" | "shadow" | "logo" | "handle"
type MoveableElementKind = Exclude<ElementKind, "shadow">
type ResizeHandle = "nw" | "ne" | "sw" | "se"

const IMAGE_TYPES: { value: DesignImageType; label: string }[] = [
  { value: "editorial", label: "Photo" }, { value: "product", label: "3D product" },
  { value: "illustration", label: "Illustration" }, { value: "diagram", label: "Diagram" },
  { value: "none", label: "No image" },
]
const ELEMENT_LABELS: Record<ElementKind, string> = { title: "Text", image: "Image", shadow: "Shadow", logo: "Logo", handle: "Handle" }
const TRANSFORM_KEYS: Record<MoveableElementKind, "titleTransform" | "imageTransform" | "logoTransform" | "handleTransform"> = {
  title: "titleTransform", image: "imageTransform", logo: "logoTransform", handle: "handleTransform",
}
const PALETTES = [
  { name: "Studio light", background: "#F6F4F0", textColor: "#252420", highlightTextColor: "#C74726", accentColor: "#C74726" },
  { name: "Studio black", background: "#000000", textColor: "#F6F4F0", highlightTextColor: "#C74726", accentColor: "#C74726" },
]
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))
const round = (value: number) => Math.round(value * 10) / 10
function nearestPosition(transform: ElementTransform): DesignPosition {
  const x = transform.x + transform.width / 2, y = transform.y + transform.height / 2
  return ((y < 34 ? "top" : y > 66 ? "bottom" : "middle") + "-" + (x < 34 ? "left" : x > 66 ? "right" : "center")) as DesignPosition
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  const labelId = React.useId()
  return <label className="design-field"><span id={labelId}>{label}</span>{React.isValidElement(children)
    ? React.cloneElement(children as React.ReactElement<{ "aria-labelledby"?: string }>, { "aria-labelledby": labelId })
    : children}</label>
}
function RangeField({ label, value, min, max, suffix = "", onChange }: {
  label: string; value: number; min: number; max: number; suffix?: string; onChange: (value: number) => void
}) {
  return <label className="design-range-field"><span><strong>{label}</strong><output>{value}{suffix}</output></span>
    <input aria-label={label} type="range" value={value} min={min} max={max} onChange={(e) => onChange(Number(e.target.value))} /></label>
}
type Interaction = {
  mode: "move" | "resize"
  handle?: ResizeHandle
  pointerId: number
  startX: number
  startY: number
  start: ElementTransform
  startScalar?: number
}

function CanvasElement({
  kind,
  transform,
  selected,
  canvasRef,
  scalar,
  scalarRange,
  onSelect,
  onTransform,
  children,
  className = "",
}: {
  kind: MoveableElementKind
  transform: ElementTransform
  selected: boolean
  canvasRef: React.RefObject<HTMLDivElement | null>
  scalar?: number
  scalarRange?: [number, number]
  onSelect: () => void
  onTransform: (transform: ElementTransform, scalar?: number) => void
  children: React.ReactNode
  className?: string
}) {
  const interaction = React.useRef<Interaction | null>(null)

  function begin(
    event: React.PointerEvent<HTMLDivElement | HTMLButtonElement>,
    mode: Interaction["mode"],
    handle?: ResizeHandle,
  ) {
    if (event.button != 0) return
    event.stopPropagation()
    onSelect()
    if (transform.locked) return
    event.currentTarget.setPointerCapture(event.pointerId)
    interaction.current = {
      mode,
      handle,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      start: { ...transform },
      startScalar: scalar,
    }
  }

  function move(event: React.PointerEvent<HTMLDivElement | HTMLButtonElement>) {
    const active = interaction.current
    const canvas = canvasRef.current
    if (!active || active.pointerId !== event.pointerId || !canvas) return
    const bounds = canvas.getBoundingClientRect()
    const dx = ((event.clientX - active.startX) / bounds.width) * 100
    const dy = ((event.clientY - active.startY) / bounds.height) * 100
    const start = active.start

    if (active.mode === "move") {
      onTransform({
        ...start,
        x: round(clamp(start.x + dx, 0, 100 - start.width)),
        y: round(clamp(start.y + dy, 0, 100 - start.height)),
      })
      return
    }

    const minWidth = kind === "logo" ? 3 : kind === "handle" ? 10 : 12
    const minHeight = kind === "logo" ? 3 : kind === "handle" ? 3 : 7
    const handle = active.handle ?? "se"
    let x = start.x
    let y = start.y
    let width = start.width
    let height = start.height

    if (handle.includes("e")) width = clamp(start.width + dx, minWidth, 100 - start.x)
    if (handle.includes("s")) height = clamp(start.height + dy, minHeight, 100 - start.y)
    if (handle.includes("w")) {
      const nextX = clamp(start.x + dx, 0, start.x + start.width - minWidth)
      width = start.width + start.x - nextX
      x = nextX
    }
    if (handle.includes("n")) {
      const nextY = clamp(start.y + dy, 0, start.y + start.height - minHeight)
      height = start.height + start.y - nextY
      y = nextY
    }

    const scaleRatio = Math.max(width / start.width, height / start.height)
    const nextScalar =
      active.startScalar !== undefined && scalarRange
        ? Math.round(clamp(active.startScalar * scaleRatio, scalarRange[0], scalarRange[1]))
        : undefined
    onTransform(
      { ...start, x: round(x), y: round(y), width: round(width), height: round(height) },
      nextScalar,
    )
  }

  function finish(event: React.PointerEvent<HTMLDivElement | HTMLButtonElement>) {
    if (interaction.current?.pointerId === event.pointerId) interaction.current = null
  }

  function nudge(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(); return }
    if (transform.locked) return
    const amount = event.shiftKey ? 2 : 0.5
    const delta = {
      ArrowLeft: [-amount, 0],
      ArrowRight: [amount, 0],
      ArrowUp: [0, -amount],
      ArrowDown: [0, amount],
    }[event.key]
    if (!delta) return
    event.preventDefault()
    onTransform({
      ...transform,
      x: round(clamp(transform.x + delta[0], 0, 100 - transform.width)),
      y: round(clamp(transform.y + delta[1], 0, 100 - transform.height)),
    })
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${ELEMENT_LABELS[kind]} layer`}
      aria-pressed={selected}
      className={`design-canvas-element design-canvas-element--${kind} ${className}`}
      data-selected={selected}
      data-locked={transform.locked}
      style={{
        left: `${transform.x}%`,
        top: `${transform.y}%`,
        width: `${transform.width}%`,
        height: `${transform.height}%`,
      }}
      onPointerDown={(event) => begin(event, "move")}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={finish}
      onKeyDown={nudge}
      onFocus={(event) => { if (event.target === event.currentTarget) onSelect() }}
    >
      {children}
      {selected && !transform.locked
        ? (["nw", "ne", "sw", "se"] as ResizeHandle[]).map((handle) => (
            <button
              key={handle}
              type="button"
              tabIndex={-1}
              aria-label={`Resize ${ELEMENT_LABELS[kind]} from ${handle}`}
              className={`design-resize-handle design-resize-handle--${handle}`}
              onPointerDown={(event) => begin(event, "resize", handle)}
              onPointerMove={move}
              onPointerUp={finish}
              onPointerCancel={finish}
            />
          ))
        : null}
      {selected && transform.locked ? (
        <span className="design-element-lock"><Lock /></span>
      ) : null}
    </div>
  )
}


const TEMPLATE_COPY: Record<string, { cover: [string, string]; inside: [string, string]; body: string }> = {
  "editorial-signal": { cover: ["A new", "perspective."], inside: ["Look a little", "closer."], body: "The stories that matter deserve a different point of view." },
  "newsroom-grid": { cover: ["The next", "big shift."], inside: ["What changes", "from here?"], body: "The context behind the headline. The details worth knowing." },
  "minimal-mono": { cover: ["Less.", "But better."], inside: ["Room for", "what matters."], body: "A considered idea. Space to breathe. Nothing more than you need." },
  "product-focus": { cover: ["Made for", "what's next."], inside: ["Small details.", "Big difference."], body: "Thoughtful design turns an everyday tool into something useful." },
  "bold-type": { cover: ["MAKE IT", "MATTER."], inside: ["Stop the", "scroll."], body: "Lead with one big idea. Give people a reason to remember it." },
}
function DesignCanvas({ design, surface, selectedElement, preview, thumbnail = false, onSelectElement, onElementTransform }: {
  design: CarouselDesign; surface: Surface; selectedElement: ElementKind | null; preview: boolean; thumbnail?: boolean
  onSelectElement: (kind: ElementKind | null) => void
  onElementTransform: (kind: MoveableElementKind, transform: ElementTransform, scalar?: number) => void
}) {
  const canvasRef = React.useRef<HTMLDivElement>(null)
  const slide = design[surface] ?? design.inside
  const copy = TEMPLATE_COPY[design.id]
  const title = surface === "cta" ? ["Stay curious.", "Follow for more."] : copy?.[surface] ?? (surface === "cover" ? ["Good ideas.", "Great stories."] : ["Make every", "swipe count."])
  const photo = design.id === "editorial-signal" || slide.titleAlign === "center" ? "editorial-canyon" : "newsroom-atrium"
  const visual = design.id === "minimal-mono" ? "research-lens" : slide.imageType === "product" ? "carousel-sculpture" : "design-stylus"
  const font = slide.fontFamily === "serif" ? "Georgia, serif" : slide.fontFamily === "condensed" ? "'Arial Narrow', Arial, sans-serif" : "Arial, sans-serif"
  function object(kind: MoveableElementKind, children: React.ReactNode, scalar?: number, scalarRange?: [number, number]) {
    const transform = slide[TRANSFORM_KEYS[kind]]
    if (thumbnail) return <div className={"design-canvas-element design-canvas-element--" + kind} style={{ left: transform.x + "%", top: transform.y + "%", width: transform.width + "%", height: transform.height + "%" }}>{children}</div>
    return <CanvasElement kind={kind} transform={surface === "cover" && kind === "image" ? { ...transform, locked: true } : transform}
      selected={!preview && selectedElement === kind} canvasRef={canvasRef} scalar={scalar} scalarRange={scalarRange}
      onSelect={() => onSelectElement(kind)} onTransform={(next, size) => onElementTransform(kind, next, size)}>{children}</CanvasElement>
  }
  return <div className="design-canvas" ref={canvasRef} data-surface={surface} data-preview={preview} data-thumbnail={thumbnail} data-template={design.id}
    style={{ background: slide.background, color: slide.textColor }} onPointerDown={thumbnail ? undefined : () => onSelectElement(null)}>
    <div className="simple-slide-content" inert={preview}>
      {slide.imageType !== "none" && object("image",
        <div className="simple-slide-visual" data-cover={surface === "cover"} data-type={slide.imageType}
          style={{ background: slide.background, color: slide.accentColor }}>
          {slide.imageType === "editorial"
            ? <img src={"/illustrations/templates/" + photo + "-640.webp"} srcSet={"/illustrations/templates/" + photo + "-160.webp 160w, /illustrations/templates/" + photo + "-640.webp 640w"} sizes={thumbnail ? "140px" : "(max-width: 767px) 90vw, 480px"} alt="Sample editorial photograph" draggable={false} />
            : slide.imageType === "diagram"
              ? <svg viewBox="0 0 500 300" role="img" aria-label="Sample idea-to-carousel diagram"><path d="M100 150H400" stroke="currentColor" strokeWidth="3" strokeDasharray="6 8" />
                  {[75, 215, 355].map((x, i) => <g key={x}><rect x={x} y="94" width="80" height="112" rx="12" fill={slide.background} stroke="currentColor" strokeWidth="2"/><rect x={x + 14} y="114" width="52" height="42" rx="5" fill="currentColor" opacity={0.2 + i * 0.25}/><path d={"M" + (x + 14) + " 174h38m-38 12h24"} stroke={slide.textColor} strokeWidth="3"/></g>)}</svg>
              : <img src={"/illustrations/" + visual + (thumbnail ? "-160.webp" : visual === "carousel-sculpture" ? "-640.webp" : "-320.webp")} alt="Sample dimensional carousel artwork" draggable={false} />}
        </div>, slide.imageScale, [30, 100])}
      {surface === "cover" && <div className="simple-slide-shadow" />}
      {object("title", <div className="simple-slide-text" style={{ fontFamily: font, fontSize: (slide.titleSize / 10.8) + "cqw", textAlign: slide.titleAlign }}>
        <div>{title[0]}<br /><span style={{ color: slide.highlightTextColor }}>{title[1]}</span></div>
        {surface !== "cover" && <p style={{ fontSize: "3.33cqw" }}>{surface === "cta" ? "The next story is worth a swipe. Join the conversation." : copy?.body ?? "One clear idea. A little curiosity. Something worth sharing."}</p>}
      </div>, slide.titleSize, [44, 160])}
      {design.logoVisible && slide.logoVisible && object("logo", <img className="simple-slide-logo" src={design.logoDataUrl || "/illustrations/carousel-sculpture-160.webp"} alt={design.logoDataUrl ? "Your design logo" : "Sample brand logo"} draggable={false} />, design.logoSize, [24, 120])}
      {design.handleVisible && slide.handleVisible && object("handle", <span className="design-canvas-handle" style={{ fontSize: (design.handleSize / 10.8) + "cqw" }}>{design.handleText || "@yourhandle"}</span>, design.handleSize, [16, 64])}
    </div>
  </div>
}

export function DesignsRoute() {
  const [designs, setDesigns, syncStatus] = useCarouselDesigns()
  const [selectedId, setSelectedId] = React.useState(() => designs.find(d => d.id === "studio-light")?.id ?? designs[0]?.id ?? "")
  const [surface, setSurface] = React.useState<Surface>("cover")
  const [selectedElement, setSelectedElement] = React.useState<ElementKind | null>(null)
  const [preparingLogo, setPreparingLogo] = React.useState(false)
  const [preview, setPreview] = React.useState(false)
  const [templatesOpen, setTemplatesOpen] = React.useState(false)
  const templateBrowser = React.useRef<HTMLDivElement>(null)
  const templateTrigger = React.useRef<HTMLButtonElement>(null)
  React.useEffect(() => {
    if (!templatesOpen) return
    const closeOutside = (event: PointerEvent) => {
      if (!templateBrowser.current?.contains(event.target as Node)) setTemplatesOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setTemplatesOpen(false); templateTrigger.current?.focus() }
    }
    window.addEventListener("pointerdown", closeOutside)
    window.addEventListener("keydown", escape)
    return () => { window.removeEventListener("pointerdown", closeOutside); window.removeEventListener("keydown", escape) }
  }, [templatesOpen])
  const [history, setHistory] = React.useState<CarouselDesign[]>([])
  const gesture = React.useRef({ active: false, recorded: false })
  const selected = designs.find(d => d.id === selectedId) ?? designs[0]
  const slide = selected?.[surface] ?? selected?.inside
  React.useEffect(() => {
    const finish = () => { gesture.current.active = false }
    window.addEventListener("pointerup", finish)
    window.addEventListener("pointercancel", finish)
    return () => { window.removeEventListener("pointerup", finish); window.removeEventListener("pointercancel", finish) }
  }, [])
  function selectDesign(id: string) {
    setSelectedId(id); setSelectedElement(null); setHistory([])
  }
  function updateDesign(change: (design: CarouselDesign) => CarouselDesign) {
    if (!selected) return
    if (!gesture.current.active || !gesture.current.recorded) {
      setHistory(current => [...current.slice(-29), selected])
      gesture.current.recorded = true
    }
    setDesigns(current => current.map(d => d.id === selected.id ? change(d) : d))
  }
  function undo() {
    const previous = history.at(-1)
    if (!previous) return
    setDesigns(current => current.map(d => d.id === previous.id ? previous : d))
    setHistory(current => current.slice(0, -1))
  }
  function updateSlide(change: Partial<CarouselDesign["cover"]>) {
    updateDesign((design) => ({
      ...design,
      [surface]: { ...(design[surface] ?? design.inside), ...change },
    }))
  }

  function updateElementTransform(
    kind: MoveableElementKind,
    transform: ElementTransform,
    scalar?: number,
  ) {
    const position = nearestPosition(transform)
    updateDesign((design) => {
      const nextSlide = { ...(design[surface] ?? design.inside), [TRANSFORM_KEYS[kind]]: transform }
      if (kind === "title") {
        nextSlide.titlePosition = position
        if (scalar !== undefined) nextSlide.titleSize = scalar
      } else if (kind === "image") {
        nextSlide.imagePosition = position
        if (scalar !== undefined) nextSlide.imageScale = scalar
      }
      const next = { ...design, [surface]: nextSlide }
      if (kind === "logo") {
        next.logoPosition = position
        if (scalar !== undefined) next.logoSize = scalar
      } else if (kind === "handle") {
        next.handlePosition = position
        if (scalar !== undefined) next.handleSize = scalar
      }
      return next
    })
  }

  function patchSelectedTransform(change: Partial<ElementTransform>) {
    if (!selectedElement || selectedElement === "shadow" || !slide) return
    const key = TRANSFORM_KEYS[selectedElement]
    const current = slide[key] as ElementTransform
    const next = { ...current, ...change }
    next.width = clamp(next.width, selectedElement === "logo" ? 3 : selectedElement === "handle" ? 10 : 12, 100)
    next.height = clamp(next.height, selectedElement === "logo" ? 3 : selectedElement === "handle" ? 3 : 7, 100)
    next.x = clamp(next.x, 0, 100 - next.width)
    next.y = clamp(next.y, 0, 100 - next.height)
    updateElementTransform(selectedElement, next)
  }

  function alignSelected(axis: "left" | "center" | "right" | "top" | "middle" | "bottom") {
    if (!selectedElement || selectedElement === "shadow" || !slide) return
    const transform = slide[TRANSFORM_KEYS[selectedElement]] as ElementTransform
    const next = { ...transform }
    if (axis === "left") next.x = 8
    if (axis === "center") next.x = (100 - next.width) / 2
    if (axis === "right") next.x = 92 - next.width
    if (axis === "top") next.y = 8
    if (axis === "middle") next.y = (100 - next.height) / 2
    if (axis === "bottom") next.y = 92 - next.height
    updateElementTransform(selectedElement, next)
  }

  function resetSelected() {
    if (!selectedElement || selectedElement === "shadow") return
    const defaults: Record<MoveableElementKind, ElementTransform> = {
      title: { x: 8, y: surface === "cover" ? 62 : 12, width: 84, height: surface === "cover" ? 24 : 32, locked: false },
      image: { x: 8, y: 47, width: 84, height: 36, locked: false },
      logo: { x: 8, y: 88, width: 6, height: 5, locked: false },
      handle: { x: 17, y: 88, width: 32, height: 5, locked: false },
    }
    updateElementTransform(selectedElement, defaults[selectedElement])
  }

  function setLayerVisibility(kind: ElementKind, visible: boolean) {
    if (kind === "logo") {
      updateDesign((design) => ({
        ...design,
        logoVisible: visible ? true : design.logoVisible,
        [surface]: { ...(design[surface] ?? design.inside), logoVisible: visible },
      }))
    }
    if (kind === "handle") {
      updateDesign((design) => ({
        ...design,
        handleVisible: visible ? true : design.handleVisible,
        [surface]: { ...(design[surface] ?? design.inside), handleVisible: visible },
      }))
    }
    if (kind === "shadow") updateSlide({ shadowVisible: visible })
    if (kind === "image") updateSlide({ imageType: visible ? "editorial" : "none" })
  }


  if (!selected || !slide) return null
  const fixedCoverVisual = surface === "cover" && selectedElement === "image"
  const activeTransform = selectedElement && selectedElement !== "shadow" ? slide[TRANSFORM_KEYS[selectedElement]] : null
  const visible = (kind: ElementKind) => kind === "title" || (kind === "image" ? slide.imageType !== "none" : kind === "shadow" ? slide.shadowVisible : kind === "logo" ? selected.logoVisible && slide.logoVisible : selected.handleVisible && slide.handleVisible)

  return <div className="simple-design-editor" onPointerDownCapture={() => { gesture.current = { active: true, recorded: false } }}
    onKeyDownCapture={() => { gesture.current.active = false }}>
    <header className="simple-editor-header">
      <div className="simple-editor-heading"><StudioEmblem name="design-stylus" small /><div><h1>Design studio</h1><p>Your carousel, your way.</p></div></div>
      <div className="simple-header-actions">
        <span className="simple-save-state" role="status">{syncStatus === "synced" ? <><Check /> Saved</> : syncStatus === "offline" ? "Saved on this device · offline" : "Saving…"}</span>
        {syncStatus === "synced" && !preparingLogo ? <Button variant="brand" size="sm" asChild><Link to={"/new?design=" + encodeURIComponent(selected.id)}>Use design <ArrowRight className="size-3.5" /></Link></Button> : <Button variant="brand" size="sm" disabled>Use design</Button>}
      </div>
    </header>
    <div className="simple-editor-library">
      <label className="simple-library-select"><span className="sr-only">Saved design</span><select value={selected.id} onChange={e => selectDesign(e.target.value)}>{designs.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
      <div className="simple-template-browser" ref={templateBrowser}>
        <Button variant={templatesOpen ? "secondary" : "ghost"} size="sm" ref={templateTrigger} aria-expanded={templatesOpen} aria-controls="template-gallery" onClick={() => setTemplatesOpen(!templatesOpen)}><PanelsTopLeft className="size-3.5" /> Templates</Button>
        {templatesOpen && <section className="simple-template-panel" id="template-gallery" aria-label="Visual templates">
          <div className="simple-template-panel-heading"><strong>Find your starting point</strong><span>Choose a look, then make it yours.</span></div>
          <div className="simple-template-gallery">{designs.map(design => <button key={design.id} type="button" aria-label={"Choose " + design.name} aria-pressed={selected.id === design.id} onClick={() => { selectDesign(design.id); setTemplatesOpen(false); templateTrigger.current?.focus() }}>
            <div className="simple-template-card-art" aria-hidden="true"><DesignCanvas design={design} surface="cover" selectedElement={null} preview thumbnail onSelectElement={() => undefined} onElementTransform={() => undefined} /></div>
            <strong>{design.name}</strong>
          </button>)}</div>
        </section>}
      </div>
      <Button variant="ghost" size="sm" onClick={() => { const created = newDesign(); setDesigns(current => [...current, created]); selectDesign(created.id) }}><Plus className="size-3.5" /> New</Button>
      <Button variant="ghost" size="icon" aria-label="Duplicate design" title="Duplicate design" onClick={() => { const copy = duplicateDesign(selected); setDesigns(current => [...current, copy]); selectDesign(copy.id) }}><Copy className="size-3.5" /></Button>
      <div className="simple-library-spacer" />
      <Button variant="ghost" size="icon" aria-label="Undo last edit" title="Undo last edit" disabled={!history.length} onClick={undo}><Undo2 className="size-4" /></Button>
      <Button variant={preview ? "secondary" : "ghost"} size="sm" aria-pressed={preview} onClick={() => setPreview(!preview)}><Eye className="size-3.5" />{preview ? "Edit" : "Preview"}</Button>
    </div>
    <section className="simple-editor-workspace" aria-label="Carousel preview">
      <div className="simple-stage-hint"><MousePointer2 /><span>{preview ? "A clean look at your layout" : "Select an object. Drag to move. Pull a corner to resize."}</span></div>
      <div className="simple-canvas-fit"><DesignCanvas design={selected} surface={surface} selectedElement={selectedElement} preview={preview}
        onSelectElement={setSelectedElement} onElementTransform={updateElementTransform} /></div>
      <div className="simple-slide-switcher" role="group" aria-label="Slide type">{(["cover", "inside", "cta"] as const).map((item, index) =>
        <button key={item} type="button" aria-pressed={surface === item} onClick={() => { setSurface(item); setSelectedElement(null) }}>
          <span className="simple-mini-slide simple-real-mini" aria-hidden="true"><DesignCanvas design={selected} surface={item} selectedElement={null} preview thumbnail onSelectElement={() => undefined} onElementTransform={() => undefined} /></span>
          <span><strong>{item === "cover" ? "Cover" : item === "cta" ? "CTA" : "Inside slide"}</strong><small>{String(index + 1).padStart(2, "0")}</small></span>
        </button>)}</div>
      <p className="simple-preview-note">Sample copy and media. Your research shapes the final content. All three layouts use your saved logo and handle.</p>
    </section>
    <aside className="simple-editor-controls" aria-label="Design controls">
      <section className="simple-control-section">
        <h2>Make it yours</h2>
        <Field label="Design name"><input key={selected.id + ":" + selected.name} defaultValue={selected.name} maxLength={120} onBlur={e => {
          const name = e.target.value.trim() || "Untitled design"; e.target.value = name
          if (name !== selected.name) updateDesign(d => ({ ...d, name }))
        }} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur() }} /></Field>
        <div className="simple-palette-list" role="group" aria-label="Apply palette to all slides">
          {PALETTES.map(({ name, ...colors }) => <button key={name} type="button" aria-pressed={selected.inside.background.toLowerCase() === colors.background.toLowerCase() && selected.inside.textColor.toLowerCase() === colors.textColor.toLowerCase()}
            onClick={() => updateDesign(d => ({ ...d, cover: { ...d.cover, ...colors, textColor: "#F6F4F0", highlightTextColor: colors.highlightTextColor === "#252420" ? "#F6F4F0" : "#C74726" }, inside: { ...d.inside, ...colors }, cta: { ...(d.cta ?? d.inside), ...colors } }))}>
            <span aria-hidden="true" style={{ background: colors.background, color: colors.highlightTextColor, borderColor: colors.textColor + "33" }}>Aa</span>{name}
          </button>)}
        </div>
        <div className="simple-color-row">
          {([{ key: "background", label: "Background" }, { key: "textColor", label: "Text color" }, { key: "highlightTextColor", label: "Accent" }] as const).filter(({ key }) => surface !== "cover" || key !== "background").map(({ key, label }) =>
            <label key={key}><input type="color" aria-label={label} value={slide[key]} onChange={e => updateSlide(key === "highlightTextColor" ? { highlightTextColor: e.target.value, accentColor: e.target.value } : { [key]: e.target.value })} /><span>{label}</span></label>)}
        </div>
      </section>
      <DesignBranding key={selected.id} design={selected} onChange={updateDesign} onBusy={setPreparingLogo} />
      <DesignGenerationSettings design={selected} onChange={updateDesign} />
      <section className="simple-control-section">
        <h2>Objects <span>{surface === "cover" ? "Cover" : surface === "cta" ? "CTA" : "Inside slide"}</span></h2>
        <div className="simple-object-list" role="group" aria-label="Select an object">
          {(surface === "cover" ? ["image", "shadow", "title", "logo", "handle"] as const : ["title", "image", "logo", "handle"] as const).map(kind =>
            <button key={kind} type="button" aria-pressed={selectedElement === kind} onClick={() => { setPreview(false); setSelectedElement(kind) }}>
              {kind === "title" ? <Type /> : kind === "image" ? <ImageIcon /> : kind === "shadow" ? <Lock /> : <span className="simple-object-glyph" aria-hidden="true">{kind === "logo" ? "C" : "@"}</span>}
              {surface === "cover" && kind === "image" ? "Cover media" : kind === "shadow" ? "Black shadow" : ELEMENT_LABELS[kind]}{kind === "shadow" && <small>Always on</small>}{!visible(kind) && <small>Hidden</small>}
            </button>)}
        </div>
        {!selectedElement && <p className="simple-control-help">Click the slide or choose an object to edit it.</p>}
      </section>
      {selectedElement && <section className="simple-control-section simple-context-controls">
        <h2>{ELEMENT_LABELS[selectedElement]}
          {activeTransform && !fixedCoverVisual && <button type="button" className="simple-lock-button" aria-label={activeTransform.locked ? "Unlock object" : "Lock object"}
            onClick={() => patchSelectedTransform({ locked: !activeTransform.locked })}>{activeTransform.locked ? <Lock /> : <Unlock />}</button>}
        </h2>
        {selectedElement === "title" && <>
          <Field label="Font"><select value={slide.fontFamily} onChange={e => updateSlide({ fontFamily: e.target.value as typeof slide.fontFamily })}><option value="sans">Modern sans</option><option value="serif">Editorial serif</option><option value="condensed">Bold condensed</option></select></Field>
          <RangeField label="Text size" value={slide.titleSize} min={44} max={160} onChange={titleSize => updateSlide({ titleSize })} />
          <div className="simple-alignment" role="group" aria-label="Text alignment">{([["left", AlignLeft], ["center", AlignCenter], ["right", AlignRight]] as const).map(([align, Icon]) =>
            <button key={align} type="button" aria-label={"Align text " + align} aria-pressed={slide.titleAlign === align} onClick={() => updateSlide({ titleAlign: align })}><Icon /></button>)}</div>
        </>}
        {selectedElement === "image" && (surface === "cover"
          ? <p className="simple-control-help">Your searched and trimmed video fills the whole cover. An image cover uses the same layout. Media is cropped to fit automatically.</p>
          : <><Field label="Image style"><select value={slide.imageType} onChange={e => updateSlide({ imageType: e.target.value as DesignImageType })}>{IMAGE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}</select></Field>
            <p className="simple-control-help">Drag the image and resize its corners to set where your visual goes.</p></>)}
        {selectedElement === "shadow" && <p className="simple-control-help">Always on. A soft fade leads into a pitch-black base behind the title and branding, on both video and image covers.</p>}
        {(selectedElement === "logo" || selectedElement === "handle") && <>
          <label className="simple-toggle"><input type="checkbox" checked={visible(selectedElement)} onChange={e => setLayerVisibility(selectedElement, e.target.checked)} />Show {ELEMENT_LABELS[selectedElement].toLowerCase()}</label>
          <p className="simple-control-help">Set your {selectedElement === "logo" ? "logo" : "handle"} in Your branding above. Drag it here to place it.</p>
        </>}
        {activeTransform && !fixedCoverVisual && visible(selectedElement) && <details className="simple-placement">
          <summary>Position & size</summary>
          <p className="simple-control-help">Arrow keys move a selected object. Hold Shift for bigger steps.</p>
          <div className="simple-geometry">{(["x", "y", "width", "height"] as const).map(key => <Field key={key} label={key === "x" ? "Left %" : key === "y" ? "Top %" : key === "width" ? "Width %" : "Height %"}>
            <input type="number" step="0.5" min={key === "x" || key === "y" ? 0 : 3} max={100} value={activeTransform[key]} disabled={activeTransform.locked} onChange={e => {
              if (e.target.value !== "" && Number.isFinite(e.target.valueAsNumber)) patchSelectedTransform({ [key]: e.target.valueAsNumber })
            }} /></Field>)}</div>
          <div className="simple-placement-actions"><Button variant="secondary" size="sm" disabled={activeTransform.locked} onClick={() => alignSelected("center")}>Center</Button>
            <Button variant="ghost" size="sm" disabled={activeTransform.locked} onClick={resetSelected}><RotateCcw className="size-3" />Reset</Button></div>
        </details>}
      </section>}
      <details className="simple-design-options"><summary>Design options</summary>
        {PREBUILT_DESIGNS.some(d => d.id === selected.id) ? <p className="simple-control-help">This is a starter design. Duplicate it to keep a separate version.</p> :
        <Button variant="ghost" size="sm" disabled={designs.length < 2} onClick={() => {
          const removed = selected
          const remaining = designs.filter(d => d.id !== selected.id)
          setDesigns(remaining); selectDesign(remaining[0].id)
          toast("Design deleted", { action: { label: "Undo", onClick: () => { setDesigns(current => current.some(d => d.id === removed.id) ? current : [...current, removed]); selectDesign(removed.id) } } })
        }}><Trash2 className="size-3.5" />Delete this design</Button>}
      </details>
    </aside>
  </div>
}
