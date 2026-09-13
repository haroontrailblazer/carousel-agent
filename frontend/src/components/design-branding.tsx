import * as React from "react"
import { ImagePlus, Loader2, RefreshCw, Trash2 } from "lucide-react"
import { type CarouselDesign } from "@/lib/designs"
import { loadDesignLogo } from "@/lib/design-logo"
import { DesignLinks } from "./design-links"
import { DesignLogo, LogoBackground } from "./design-logo"
import { LogoCropDialog } from "./logo-crop-dialog"
import "./design-branding.css"

export function DesignBranding({ design, onChange, onBusy }: {
  design: CarouselDesign
  onChange: (change: (design: CarouselDesign) => CarouselDesign) => void
  onBusy: (busy: boolean) => void
}) {
  const [handle, setHandle] = React.useState(design.handleText ?? "")
  const [error, setError] = React.useState("")
  const [working, setWorking] = React.useState(false)
  const [cropSource, setCropSource] = React.useState<HTMLImageElement | null>(null)
  const input = React.useRef<HTMLInputElement>(null)
  const picker = React.useRef<HTMLButtonElement>(null)
  const alive = React.useRef(true)
  React.useEffect(() => { setHandle(design.handleText ?? "") }, [design.handleText])
  React.useEffect(() => { onBusy(working || cropSource !== null) }, [working, cropSource, onBusy])
  React.useEffect(() => {
    alive.current = true
    return () => { alive.current = false; onBusy(false) }
  }, [onBusy])

  function saveHandle() {
    const text = handle.trim().replace(/^@/, "")
    if (text && !/^[A-Za-z0-9._]{1,30}$/.test(text)) {
      setError("Use up to 30 letters, numbers, periods or underscores.")
      return
    }
    setError("")
    const normalized = text ? "@" + text : ""
    setHandle(normalized)
    if (normalized !== (design.handleText ?? "")) onChange(current => ({
      ...current, handleText: normalized,
      ...(normalized ? { handleVisible: true, cover: { ...current.cover, handleVisible: true }, inside: { ...current.inside, handleVisible: true } } : {}),
    }))
  }

  async function pickLogo(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) return
    setWorking(true); setError("")
    try {
      const image = await loadDesignLogo(file)
      if (!alive.current) return
      setCropSource(image)
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : "Could not open this logo.")
    } finally {
      if (alive.current) setWorking(false)
    }
  }

  return <section className="simple-control-section simple-design-branding">
    <h2>Your branding <span>For this design</span></h2>
    <div className="branding-logo" aria-busy={working}>
      <button ref={picker} className="branding-logo-picker" type="button" disabled={working} onClick={() => input.current?.click()} aria-label={design.logoDataUrl ? "Replace logo" : "Upload logo"}>
        <span className="branding-logo-preview">{design.logoDataUrl ? <DesignLogo src={design.logoDataUrl} background={design.logoBackground} label="Logo for this design" /> : <img src="/illustrations/carousel-sculpture-160.webp" alt="" />}</span>
        <span className="branding-logo-copy"><strong>{working ? "Preparing logo…" : design.logoDataUrl ? "Replace logo" : "Upload your logo"}</strong><span>{design.logoDataUrl ? "Choose an image and adjust the crop" : "Upload, crop and preview your logo"}</span></span>
        <span className="branding-logo-action" aria-hidden="true">{working ? <Loader2 className="animate-spin" /> : design.logoDataUrl ? <RefreshCw /> : <ImagePlus />}</span>
      </button>
      <div className="branding-logo-details"><span>PNG, JPG or WebP · Max 5 MB</span>
        {design.logoDataUrl && <button type="button" aria-label="Remove design logo" disabled={working} onClick={() => onChange(current => ({ ...current, logoDataUrl: "" }))}><Trash2 aria-hidden="true" /> Remove</button>}
      </div>
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" aria-label="Upload design logo" className="sr-only" tabIndex={-1} onChange={pickLogo} disabled={working} />
    </div>
    {design.logoDataUrl && <LogoBackground value={design.logoBackground ?? ""} onChange={logoBackground => onChange(current => ({ ...current, logoBackground }))} />}
    {cropSource && <LogoCropDialog image={cropSource} design={design} onClose={() => setCropSource(null)} restoreFocus={() => picker.current?.focus()} onApply={(logoDataUrl, logoBackground) => {
      onChange(current => ({ ...current, logoDataUrl, logoBackground, logoVisible: true,
        cover: { ...current.cover, logoVisible: true }, inside: { ...current.inside, logoVisible: true }, cta: { ...(current.cta ?? current.inside), logoVisible: true },
      }))
      setCropSource(null)
    }} />}
    <label className="design-field branding-handle"><span>Instagram handle</span><input aria-label="Design handle" value={handle} placeholder="@yourbrand" maxLength={31} autoCapitalize="none" autoCorrect="off" spellCheck={false}
      onChange={event => { setHandle(event.target.value); setError("") }} onBlur={saveHandle} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur() }} /></label>
    {error && <p className="simple-branding-error" role="alert">{error}</p>}
    <p className="simple-control-help">Your logo and handle appear on this design’s slides. Leave them empty to use your connected account.</p>
    <DesignLinks design={design} onChange={onChange} />
  </section>
}
