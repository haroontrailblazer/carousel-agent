import * as React from "react"
import {
  Copy,
  Image as ImageIcon,
  Plus,
  Save,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  type CarouselDesign,
  type DesignImageType,
  type DesignPosition,
  duplicateDesign,
  newDesign,
  useCarouselDesigns,
} from "@/lib/designs"

const POSITIONS: DesignPosition[] = [
  "top-left",
  "top-center",
  "top-right",
  "middle-left",
  "middle-center",
  "middle-right",
  "bottom-left",
  "bottom-center",
  "bottom-right",
]

const IMAGE_TYPES: { value: DesignImageType; label: string }[] = [
  { value: "editorial", label: "Editorial photo" },
  { value: "product", label: "Product render" },
  { value: "illustration", label: "Illustration" },
  { value: "diagram", label: "Diagram" },
  { value: "none", label: "No image" },
]

function PositionGrid({
  value,
  onChange,
}: {
  value: DesignPosition
  onChange: (position: DesignPosition) => void
}) {
  return (
    <div className="design-position-grid" aria-label="Position">
      {POSITIONS.map((position) => (
        <button
          key={position}
          type="button"
          aria-label={position.replaceAll("-", " ")}
          aria-pressed={position === value}
          onClick={() => onChange(position)}
        >
          <span />
        </button>
      ))}
    </div>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
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
          value={value}
          min={min}
          max={max}
          onChange={(event) =>
            onChange(Math.min(max, Math.max(min, Number(event.target.value))))
          }
        />
        <span>{suffix}</span>
      </div>
    </Field>
  )
}

function BrandMark({ design }: { design: CarouselDesign }) {
  const sharedPosition = design.logoPosition === design.handlePosition

  if (sharedPosition) {
    return (
      <div
        className={`design-canvas-brand design-pos-${design.logoPosition}`}
        style={{ fontSize: `${Math.max(11, design.handleSize * 0.42)}px` }}
      >
        {design.logoVisible && (
          <span
            className="design-canvas-logo"
            style={{ width: design.logoSize / 4, height: design.logoSize / 4 }}
          >
            C
          </span>
        )}
        {design.handleVisible && <span>@yourhandle</span>}
      </div>
    )
  }

  return (
    <>
      {design.logoVisible && (
        <div
          className={`design-canvas-brand design-pos-${design.logoPosition}`}
        >
          <span
            className="design-canvas-logo"
            style={{ width: design.logoSize / 4, height: design.logoSize / 4 }}
          >
            C
          </span>
        </div>
      )}
      {design.handleVisible && (
        <div
          className={`design-canvas-brand design-pos-${design.handlePosition}`}
          style={{ fontSize: `${Math.max(11, design.handleSize * 0.42)}px` }}
        >
          <span>@yourhandle</span>
        </div>
      )}
    </>
  )
}

function DesignCanvas({
  design,
  surface,
}: {
  design: CarouselDesign
  surface: "cover" | "inside"
}) {
  const slide = design[surface]
  const title = surface === "cover" ? "BUILD BETTER SYSTEMS" : "Ideas become systems"
  const pos = slide.titlePosition.split("-")
  const imagePos = slide.imagePosition.split("-")
  const imageStyle: React.CSSProperties = {
    width: `${Math.max(24, slide.imageScale * 0.68)}%`,
    justifySelf: imagePos[1] === "left" ? "start" : imagePos[1] === "right" ? "end" : "center",
    alignSelf: imagePos[0] === "top" ? "start" : imagePos[0] === "bottom" ? "end" : "center",
  }

  return (
    <div
      className="design-canvas"
      style={{
        background: slide.background,
        color: slide.textColor,
        padding: `${Math.max(20, slide.safeMargin / 4)}px`,
      }}
    >
      <div className="design-canvas-safe" />
      <BrandMark design={design} />
      <div
        className={`design-canvas-title design-pos-${slide.titlePosition}`}
        style={{
          color: slide.textColor,
          fontFamily:
            slide.fontFamily === "serif"
              ? "Georgia, serif"
              : slide.fontFamily === "condensed"
                ? "Arial Narrow, Arial, sans-serif"
                : "Arial, sans-serif",
          fontSize: `${Math.max(22, slide.titleSize / 4)}px`,
          textAlign: slide.titleAlign,
          alignItems: pos[1] === "left" ? "flex-start" : pos[1] === "right" ? "flex-end" : "center",
        }}
      >
        {title}
        <span style={{ background: slide.accentColor }} />
      </div>
      {slide.imageType !== "none" && (
        <div
          className={`design-canvas-image design-image-${slide.imageType} ${surface === "cover" ? "design-canvas-image--cover" : ""}`}
          style={surface === "cover" ? undefined : imageStyle}
        >
          <ImageIcon />
          <span>{IMAGE_TYPES.find((item) => item.value === slide.imageType)?.label}</span>
        </div>
      )}
      {surface === "inside" && (
        <p className="design-canvas-copy">A clear supporting thought sits beneath the headline.</p>
      )}
    </div>
  )
}

export function DesignsRoute() {
  const [designs, setDesigns] = useCarouselDesigns()
  const [selectedId, setSelectedId] = React.useState(() => designs[0]?.id ?? "")
  const [surface, setSurface] = React.useState<"cover" | "inside">("cover")
  const selected = designs.find((design) => design.id === selectedId) ?? designs[0]
  const slide = selected?.[surface]

  function updateDesign(change: (design: CarouselDesign) => CarouselDesign) {
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

  if (!selected || !slide) return null

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
                style={{
                  background: design.cover.background,
                  color: design.cover.textColor,
                  borderColor: design.cover.accentColor,
                }}
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
      </section>

      <section className="designs-stage">
        <header className="designs-toolbar">
          <div className="designs-surface-tabs" role="tablist" aria-label="Slide type">
            {(["cover", "inside"] as const).map((item) => (
              <button
                key={item}
                role="tab"
                aria-selected={surface === item}
                onClick={() => setSurface(item)}
              >
                {item === "inside" ? "Inside slide" : "Cover"}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
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
            <Button
              variant="brand"
              onClick={() => toast.success(`${selected.name} is ready to use`)}
            >
              <Save className="size-4" /> Save design
            </Button>
          </div>
        </header>
        <div className="designs-canvas-stage">
          <DesignCanvas design={selected} surface={surface} />
        </div>
        <p className="designs-stage-note">Live 4:5 preview · 1080 × 1350 output</p>
      </section>

      <aside className="designs-inspector">
        <div className="design-inspector-section">
          <h2>Design</h2>
          <Field label="Name">
            <input
              value={selected.name}
              onChange={(event) => updateDesign((design) => ({ ...design, name: event.target.value }))}
            />
          </Field>
        </div>

        <div className="design-inspector-section">
          <h2>Brand</h2>
          <div className="design-toggle-row">
            <span>Logo</span>
            <input
              type="checkbox"
              checked={selected.logoVisible}
              onChange={(event) => updateDesign((design) => ({ ...design, logoVisible: event.target.checked }))}
            />
          </div>
          <div className="design-inspector-grid">
            <div>
              <span className="design-field-label">Position</span>
              <PositionGrid
                value={selected.logoPosition}
                onChange={(logoPosition) => updateDesign((design) => ({ ...design, logoPosition }))}
              />
            </div>
            <NumberField
              label="Size"
              value={selected.logoSize}
              min={24}
              max={120}
              onChange={(logoSize) => updateDesign((design) => ({ ...design, logoSize }))}
            />
          </div>
          <div className="design-toggle-row design-divider-top">
            <span>Instagram handle</span>
            <input
              type="checkbox"
              checked={selected.handleVisible}
              onChange={(event) => updateDesign((design) => ({ ...design, handleVisible: event.target.checked }))}
            />
          </div>
          <div className="design-inspector-grid">
            <div>
              <span className="design-field-label">Position</span>
              <PositionGrid
                value={selected.handlePosition}
                onChange={(handlePosition) => updateDesign((design) => ({ ...design, handlePosition }))}
              />
            </div>
            <NumberField
              label="Font size"
              value={selected.handleSize}
              min={16}
              max={64}
              onChange={(handleSize) => updateDesign((design) => ({ ...design, handleSize }))}
            />
          </div>
        </div>

        <div className="design-inspector-section">
          <h2>{surface === "cover" ? "Cover title" : "Inside title"}</h2>
          <div className="design-inspector-grid">
            <div>
              <span className="design-field-label">Position</span>
              <PositionGrid value={slide.titlePosition} onChange={(titlePosition) => updateSlide({ titlePosition })} />
            </div>
            <NumberField label="Font size" value={slide.titleSize} min={44} max={160} onChange={(titleSize) => updateSlide({ titleSize })} />
          </div>
          <div className="design-three-fields">
            <Field label="Font">
              <select value={slide.fontFamily} onChange={(event) => updateSlide({ fontFamily: event.target.value as typeof slide.fontFamily })}>
                <option value="condensed">Condensed</option>
                <option value="sans">Sans</option>
                <option value="serif">Serif</option>
              </select>
            </Field>
            <Field label="Align">
              <select value={slide.titleAlign} onChange={(event) => updateSlide({ titleAlign: event.target.value as typeof slide.titleAlign })}>
                <option value="left">Left</option>
                <option value="center">Center</option>
                <option value="right">Right</option>
              </select>
            </Field>
            <NumberField label="Safe margin" value={slide.safeMargin} min={48} max={160} onChange={(safeMargin) => updateSlide({ safeMargin })} />
          </div>
        </div>

        <div className="design-inspector-section">
          <h2>Image</h2>
          <Field label="Image type">
            <select value={slide.imageType} onChange={(event) => updateSlide({ imageType: event.target.value as DesignImageType })}>
              {IMAGE_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
            </select>
          </Field>
          <div className="design-inspector-grid">
            <div>
              <span className="design-field-label">Position</span>
              <PositionGrid value={slide.imagePosition} onChange={(imagePosition) => updateSlide({ imagePosition })} />
            </div>
            <NumberField label="Size" value={slide.imageScale} min={30} max={100} suffix="%" onChange={(imageScale) => updateSlide({ imageScale })} />
          </div>
        </div>

        <div className="design-inspector-section">
          <h2>Colors</h2>
          <div className="design-color-grid">
            {([
              ["Background", "background"],
              ["Text", "textColor"],
              ["Accent", "accentColor"],
            ] as const).map(([label, key]) => (
              <Field key={key} label={label}>
                <input type="color" value={slide[key]} onChange={(event) => updateSlide({ [key]: event.target.value })} />
              </Field>
            ))}
          </div>
        </div>

        {designs.length > 1 && (
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
        )}
      </aside>
    </main>
  )
}
