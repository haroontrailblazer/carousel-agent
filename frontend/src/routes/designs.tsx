import * as React from "react"
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Copy,
  Eye,
  EyeOff,
  Image as ImageIcon,
  Layers3,
  Lock,
  MousePointer2,
  Move,
  Plus,
  RotateCcw,
  Save,
  Trash2,
  Unlock,
  ZoomIn,
  ZoomOut,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  type CarouselDesign,
  type DesignImageType,
  type DesignPosition,
  type ElementTransform,
  duplicateDesign,
  newDesign,
  useCarouselDesigns,
} from "@/lib/designs"

type Surface = "cover" | "inside"
type ElementKind = "title" | "image" | "logo" | "handle"
type ResizeHandle = "nw" | "ne" | "sw" | "se"

const IMAGE_TYPES: { value: DesignImageType; label: string }[] = [
  { value: "editorial", label: "Editorial photo" },
  { value: "product", label: "Product render" },
  { value: "illustration", label: "Illustration" },
  { value: "diagram", label: "Diagram" },
  { value: "none", label: "No image" },
]

const ELEMENT_LABELS: Record<ElementKind, string> = {
  title: "Title",
  image: "Image",
  logo: "Logo",
  handle: "Instagram handle",
}

const TRANSFORM_KEYS: Record<ElementKind, keyof CarouselDesign["cover"]> = {
  title: "titleTransform",
  image: "imageTransform",
  logo: "logoTransform",
  handle: "handleTransform",
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value))

const round = (value: number) => Math.round(value * 10) / 10

function nearestPosition(transform: ElementTransform): DesignPosition {
  const centerX = transform.x + transform.width / 2
  const centerY = transform.y + transform.height / 2
  const horizontal = centerX < 34 ? "left" : centerX > 66 ? "right" : "center"
  const vertical = centerY < 34 ? "top" : centerY > 66 ? "bottom" : "middle"
  return `${vertical}-${horizontal}` as DesignPosition
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="design-field">
      <span>{label}</span>
      {children}
    </label>
  )
}

function NumberField({
  label,
  value,
  min,
  max,
  suffix = "px",
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  suffix?: string
  onChange: (value: number) => void
}) {
  return (
    <Field label={label}>
      <div className="design-number-input">
        <input
          type="number"
          value={round(value)}
          min={min}
          max={max}
          step={suffix === "%" ? 0.5 : 1}
          onChange={(event) => onChange(clamp(Number(event.target.value), min, max))}
        />
        <span>{suffix}</span>
      </div>
    </Field>
  )
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
  kind: ElementKind
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
    >
      {children}
      {selected && !transform.locked
        ? (["nw", "ne", "sw", "se"] as ResizeHandle[]).map((handle) => (
            <button
              key={handle}
              type="button"
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

function DesignCanvas({
  design,
  surface,
  selectedElement,
  zoom,
  onSelectElement,
  onElementTransform,
}: {
  design: CarouselDesign
  surface: Surface
  selectedElement: ElementKind | null
  zoom: number
  onSelectElement: (kind: ElementKind | null) => void
  onElementTransform: (kind: ElementKind, transform: ElementTransform, scalar?: number) => void
}) {
  const canvasRef = React.useRef<HTMLDivElement>(null)
  const slide = design[surface]
  const title = surface === "cover" ? "BUILD BETTER SYSTEMS" : "Ideas become systems"
  const titleFont =
    slide.fontFamily === "serif"
      ? "Georgia, serif"
      : slide.fontFamily === "condensed"
        ? "Arial Narrow, Arial, sans-serif"
        : "Arial, sans-serif"

  return (
    <div
      ref={canvasRef}
      className="design-canvas"
      data-surface={surface}
      style={{
        background: slide.background,
        color: slide.textColor,
        width: `${31 * (zoom / 100)}rem`,
      }}
      onPointerDown={() => onSelectElement(null)}
    >
      <div className="design-canvas-safe" style={{ inset: `${(slide.safeMargin / 1080) * 100}%` }} />

      {slide.imageType !== "none" ? (
        <CanvasElement
          kind="image"
          transform={surface === "cover" ? { ...slide.imageTransform, locked: true } : slide.imageTransform}
          selected={selectedElement === "image"}
          canvasRef={canvasRef}
          scalar={slide.imageScale}
          scalarRange={[30, 100]}
          onSelect={() => onSelectElement("image")}
          onTransform={(transform, scalar) => onElementTransform("image", transform, scalar)}
          className={`design-image-${slide.imageType} ${surface === "cover" ? "design-canvas-image--cover" : ""}`}
        >
          <div className="design-canvas-image-content">
            <ImageIcon />
            <span>{IMAGE_TYPES.find((item) => item.value === slide.imageType)?.label}</span>
          </div>
        </CanvasElement>
      ) : null}

      <CanvasElement
        kind="title"
        transform={slide.titleTransform}
        selected={selectedElement === "title"}
        canvasRef={canvasRef}
        scalar={slide.titleSize}
        scalarRange={[44, 160]}
        onSelect={() => onSelectElement("title")}
        onTransform={(transform, scalar) => onElementTransform("title", transform, scalar)}
      >
        <div
          className="design-canvas-title-content"
          style={{
            color: slide.textColor,
            fontFamily: titleFont,
            fontSize: `${Math.max(18, slide.titleSize / 4)}px`,
            textAlign: slide.titleAlign,
            alignItems: slide.titleAlign === "left" ? "flex-start" : slide.titleAlign === "right" ? "flex-end" : "center",
          }}
        >
          {title}
          <span style={{ background: slide.accentColor }} />
        </div>
      </CanvasElement>

      {surface === "inside" ? (
        <p className="design-canvas-copy">A clear supporting thought sits beneath the headline.</p>
      ) : null}

      {design.logoVisible ? (
        <CanvasElement
          kind="logo"
          transform={slide.logoTransform}
          selected={selectedElement === "logo"}
          canvasRef={canvasRef}
          scalar={design.logoSize}
          scalarRange={[24, 120]}
          onSelect={() => onSelectElement("logo")}
          onTransform={(transform, scalar) => onElementTransform("logo", transform, scalar)}
        >
          <span className="design-canvas-logo">C</span>
        </CanvasElement>
      ) : null}

      {design.handleVisible ? (
        <CanvasElement
          kind="handle"
          transform={slide.handleTransform}
          selected={selectedElement === "handle"}
          canvasRef={canvasRef}
          scalar={design.handleSize}
          scalarRange={[16, 64]}
          onSelect={() => onSelectElement("handle")}
          onTransform={(transform, scalar) => onElementTransform("handle", transform, scalar)}
        >
          <span className="design-canvas-handle" style={{ fontSize: `${Math.max(10, design.handleSize * 0.42)}px` }}>
            @yourhandle
          </span>
        </CanvasElement>
      ) : null}
    </div>
  )
}

function LayerRow({
  kind,
  visible,
  locked,
  selected,
  onSelect,
  onVisibility,
}: {
  kind: ElementKind
  visible: boolean
  locked: boolean
  selected: boolean
  onSelect: () => void
  onVisibility?: () => void
}) {
  return (
    <div className="design-layer-row" data-selected={selected}>
      <button type="button" className="design-layer-select" onClick={onSelect} disabled={!visible}>
        <Move />
        <span>{ELEMENT_LABELS[kind]}</span>
        {locked ? <Lock /> : null}
      </button>
      {onVisibility ? (
        <button type="button" aria-label={`${visible ? "Hide" : "Show"} ${ELEMENT_LABELS[kind]}`} onClick={onVisibility}>
          {visible ? <Eye /> : <EyeOff />}
        </button>
      ) : null}
    </div>
  )
}

export function DesignsRoute() {
  const [designs, setDesigns] = useCarouselDesigns()
  const [selectedId, setSelectedId] = React.useState(() => designs[0]?.id ?? "")
  const [surface, setSurface] = React.useState<Surface>("cover")
  const [selectedElement, setSelectedElement] = React.useState<ElementKind | null>("title")
  const [zoom, setZoom] = React.useState(100)
  const selected = designs.find((design) => design.id === selectedId) ?? designs[0]
  const slide = selected?.[surface]

  function updateDesign(change: (design: CarouselDesign) => CarouselDesign) {
    if (!selected) return
    setDesigns((current) =>
      current.map((design) => (design.id === selected.id ? change(design) : design)),
    )
  }

  function updateSlide(change: Partial<CarouselDesign["cover"]>) {
    updateDesign((design) => ({
      ...design,
      [surface]: { ...design[surface], ...change },
    }))
  }

  function updateElementTransform(
    kind: ElementKind,
    transform: ElementTransform,
    scalar?: number,
  ) {
    const position = nearestPosition(transform)
    updateDesign((design) => {
      const nextSlide = { ...design[surface], [TRANSFORM_KEYS[kind]]: transform }
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
    if (!selectedElement || !slide) return
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
    if (!selectedElement || !slide) return
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
    if (!selectedElement) return
    const defaults: Record<ElementKind, ElementTransform> = {
      title: { x: 8, y: surface === "cover" ? 62 : 8, width: 76, height: 22, locked: false },
      image: { x: 8, y: 56, width: 84, height: 32, locked: false },
      logo: { x: 8, y: 87, width: 6, height: 5, locked: false },
      handle: { x: 16, y: 87, width: 28, height: 5, locked: false },
    }
    updateElementTransform(selectedElement, defaults[selectedElement])
  }

  function toggleVisibility(kind: ElementKind) {
    if (kind === "logo") updateDesign((design) => ({ ...design, logoVisible: !design.logoVisible }))
    if (kind === "handle") updateDesign((design) => ({ ...design, handleVisible: !design.handleVisible }))
    if (kind === "image") updateSlide({ imageType: slide?.imageType === "none" ? "editorial" : "none" })
  }

  if (!selected || !slide) return null
  const fixedCoverVisual = surface === "cover" && selectedElement === "image"
  const activeTransform = selectedElement
    ? (slide[TRANSFORM_KEYS[selectedElement]] as ElementTransform)
    : null

  return (
    <main className="designs-page">
      <section className="designs-library">
        <div className="designs-library-heading">
          <div>
            <h1>Designs</h1>
            <p>Named formats your agents can reuse.</p>
          </div>
          <Button
            variant="secondary"
            size="icon"
            title="Create a design"
            onClick={() => {
              const created = newDesign()
              setDesigns((current) => [...current, created])
              setSelectedId(created.id)
            }}
          >
            <Plus className="size-4" />
          </Button>
        </div>

        <div className="design-template-list">
          {designs.map((design) => (
            <button
              key={design.id}
              type="button"
              className="design-template-row"
              data-selected={design.id === selected.id}
              onClick={() => setSelectedId(design.id)}
            >
              <span
                className="design-template-thumb"
                style={{ background: design.cover.background, color: design.cover.textColor, borderColor: design.cover.accentColor }}
              >
                Aa
              </span>
              <span>
                <strong>{design.name}</strong>
                <small>{design.inside.imageType.replace("none", "text only")}</small>
              </span>
            </button>
          ))}
        </div>

        <div className="design-editor-tip">
          <MousePointer2 />
          <p><strong>Edit on canvas</strong><span>Click, drag, or resize any layer.</span></p>
        </div>
      </section>

      <section className="designs-stage">
        <header className="designs-toolbar">
          <div className="designs-surface-tabs" role="tablist" aria-label="Slide type">
            {(["cover", "inside"] as const).map((item) => (
              <button
                key={item}
                role="tab"
                aria-selected={surface === item}
                onClick={() => { setSurface(item); setSelectedElement("title") }}
              >
                {item === "inside" ? "Inside slide" : "Cover"}
              </button>
            ))}
          </div>
          <div className="design-toolbar-actions">
            <Button
              variant="secondary"
              onClick={() => {
                const copy = duplicateDesign(selected)
                setDesigns((current) => [...current, copy])
                setSelectedId(copy.id)
              }}
            >
              <Copy className="size-4" /> Duplicate
            </Button>
            <Button variant="brand" onClick={() => toast.success(`${selected.name} is ready to use`)}>
              <Save className="size-4" /> Save design
            </Button>
          </div>
        </header>

        <div className="designs-canvas-stage">
          <div className="design-canvas-help"><MousePointer2 /> Drag to move · use corner handles to resize</div>
          <DesignCanvas
            design={selected}
            surface={surface}
            selectedElement={selectedElement}
            zoom={zoom}
            onSelectElement={setSelectedElement}
            onElementTransform={updateElementTransform}
          />
          <div className="design-zoom-control" aria-label="Canvas zoom">
            <button type="button" aria-label="Zoom out" onClick={() => setZoom((value) => clamp(value - 10, 60, 130))}><ZoomOut /></button>
            <span>{zoom}%</span>
            <button type="button" aria-label="Zoom in" onClick={() => setZoom((value) => clamp(value + 10, 60, 130))}><ZoomIn /></button>
          </div>
        </div>
        <p className="designs-stage-note">Live 4:5 preview · 1080 × 1350 output · Arrow keys nudge selected layers</p>
      </section>

      <aside className="designs-inspector">
        <div className="design-inspector-section design-inspector-design">
          <h2>Design</h2>
          <Field label="Name">
            <input value={selected.name} onChange={(event) => updateDesign((design) => ({ ...design, name: event.target.value }))} />
          </Field>
        </div>

        <div className="design-inspector-section">
          <div className="design-inspector-heading">
            <div>
              <span className="design-field-label">Selected layer</span>
              <h2>{selectedElement ? ELEMENT_LABELS[selectedElement] : "Canvas"}</h2>
            </div>
            {activeTransform && !fixedCoverVisual ? (
              <div className="design-icon-actions">
                <button type="button" title={activeTransform.locked ? "Unlock layer" : "Lock layer"} onClick={() => patchSelectedTransform({ locked: !activeTransform.locked })}>
                  {activeTransform.locked ? <Lock /> : <Unlock />}
                </button>
                <button type="button" title="Reset position and size" onClick={resetSelected}><RotateCcw /></button>
              </div>
            ) : null}
          </div>

          {activeTransform && !fixedCoverVisual ? (
            <>
              <div className="design-transform-grid">
                <NumberField label="X" value={activeTransform.x} min={0} max={100 - activeTransform.width} suffix="%" onChange={(x) => patchSelectedTransform({ x })} />
                <NumberField label="Y" value={activeTransform.y} min={0} max={100 - activeTransform.height} suffix="%" onChange={(y) => patchSelectedTransform({ y })} />
                <NumberField label="W" value={activeTransform.width} min={3} max={100 - activeTransform.x} suffix="%" onChange={(width) => patchSelectedTransform({ width })} />
                <NumberField label="H" value={activeTransform.height} min={3} max={100 - activeTransform.y} suffix="%" onChange={(height) => patchSelectedTransform({ height })} />
              </div>
              <div className="design-arrange-row" aria-label="Align selected layer">
                <button type="button" title="Align left" onClick={() => alignSelected("left")}><AlignLeft /></button>
                <button type="button" title="Center horizontally" onClick={() => alignSelected("center")}><AlignCenter /></button>
                <button type="button" title="Align right" onClick={() => alignSelected("right")}><AlignRight /></button>
                <span />
                <button type="button" title="Align top" onClick={() => alignSelected("top")}>T</button>
                <button type="button" title="Center vertically" onClick={() => alignSelected("middle")}>M</button>
                <button type="button" title="Align bottom" onClick={() => alignSelected("bottom")}>B</button>
              </div>
            </>
          ) : fixedCoverVisual ? (
            <p className="design-inspector-empty">The cover visual is a full-canvas source background. Select the title, logo, or handle to move it freely.</p>
          ) : (
            <p className="design-inspector-empty">Select a layer on the canvas or from the Layers panel.</p>
          )}
        </div>

        {selectedElement === "title" ? (
          <div className="design-inspector-section">
            <h2>Typography</h2>
            <div className="design-inspector-grid design-inspector-grid--equal">
              <NumberField label="Font size" value={slide.titleSize} min={44} max={160} onChange={(titleSize) => updateSlide({ titleSize })} />
              <Field label="Font">
                <select value={slide.fontFamily} onChange={(event) => updateSlide({ fontFamily: event.target.value as typeof slide.fontFamily })}>
                  <option value="condensed">Condensed</option>
                  <option value="sans">Sans</option>
                  <option value="serif">Serif</option>
                </select>
              </Field>
            </div>
            <div className="design-segmented-control" aria-label="Text alignment">
              {(["left", "center", "right"] as const).map((align) => (
                <button key={align} type="button" aria-pressed={slide.titleAlign === align} onClick={() => updateSlide({ titleAlign: align })}>
                  {align === "left" ? <AlignLeft /> : align === "right" ? <AlignRight /> : <AlignCenter />}
                  <span>{align}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {selectedElement === "image" ? (
          <div className="design-inspector-section">
            <h2>Image settings</h2>
            <Field label="Visual direction">
              <select value={slide.imageType} onChange={(event) => updateSlide({ imageType: event.target.value as DesignImageType })}>
                {IMAGE_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
              </select>
            </Field>
            <NumberField label="Generation scale" value={slide.imageScale} min={30} max={100} suffix="%" onChange={(imageScale) => updateSlide({ imageScale })} />
          </div>
        ) : null}

        {selectedElement === "logo" ? (
          <div className="design-inspector-section">
            <h2>Logo settings</h2>
            <div className="design-toggle-row"><span>Show connected account logo</span><input type="checkbox" checked={selected.logoVisible} onChange={(event) => updateDesign((design) => ({ ...design, logoVisible: event.target.checked }))} /></div>
            <NumberField label="Output size" value={selected.logoSize} min={24} max={120} onChange={(logoSize) => updateDesign((design) => ({ ...design, logoSize }))} />
          </div>
        ) : null}

        {selectedElement === "handle" ? (
          <div className="design-inspector-section">
            <h2>Instagram handle</h2>
            <div className="design-toggle-row"><span>Show connected account handle</span><input type="checkbox" checked={selected.handleVisible} onChange={(event) => updateDesign((design) => ({ ...design, handleVisible: event.target.checked }))} /></div>
            <NumberField label="Font size" value={selected.handleSize} min={16} max={64} onChange={(handleSize) => updateDesign((design) => ({ ...design, handleSize }))} />
          </div>
        ) : null}

        <div className="design-inspector-section">
          <h2>Canvas</h2>
          <div className="design-color-grid">
            {(["background", "textColor", "accentColor"] as const).map((key) => (
              <Field key={key} label={key === "textColor" ? "Text" : key === "accentColor" ? "Accent" : "Background"}>
                <input type="color" value={slide[key]} onChange={(event) => updateSlide({ [key]: event.target.value })} />
              </Field>
            ))}
          </div>
          <NumberField label="Safe margin" value={slide.safeMargin} min={48} max={160} onChange={(safeMargin) => updateSlide({ safeMargin })} />
        </div>

        <div className="design-inspector-section">
          <div className="design-inspector-heading"><h2>Layers</h2><Layers3 /></div>
          <div className="design-layer-list">
            <LayerRow kind="handle" visible={selected.handleVisible} locked={slide.handleTransform.locked} selected={selectedElement === "handle"} onSelect={() => setSelectedElement("handle")} onVisibility={() => toggleVisibility("handle")} />
            <LayerRow kind="logo" visible={selected.logoVisible} locked={slide.logoTransform.locked} selected={selectedElement === "logo"} onSelect={() => setSelectedElement("logo")} onVisibility={() => toggleVisibility("logo")} />
            <LayerRow kind="title" visible locked={slide.titleTransform.locked} selected={selectedElement === "title"} onSelect={() => setSelectedElement("title")} />
            <LayerRow kind="image" visible={slide.imageType !== "none"} locked={surface === "cover" || slide.imageTransform.locked} selected={selectedElement === "image"} onSelect={() => setSelectedElement("image")} onVisibility={() => toggleVisibility("image")} />
          </div>
        </div>

        {designs.length > 1 ? (
          <Button
            variant="ghost"
            className="w-full text-[var(--destructive)]"
            onClick={() => {
              const remaining = designs.filter((design) => design.id !== selected.id)
              setDesigns(remaining)
              setSelectedId(remaining[0].id)
            }}
          >
            <Trash2 className="size-4" /> Delete design
          </Button>
        ) : null}
      </aside>
    </main>
  )
}
