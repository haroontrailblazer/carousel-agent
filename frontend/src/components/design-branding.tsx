import * as React from "react"
import { Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { type CarouselDesign } from "@/lib/designs"
import { prepareDesignLogo } from "@/lib/design-logo"

export function DesignBranding({ design, onChange, onBusy }: {
  design: CarouselDesign
  onChange: (change: (design: CarouselDesign) => CarouselDesign) => void
  onBusy: (busy: boolean) => void
}) {
  const [handle, setHandle] = React.useState(design.handleText ?? "")
  const [error, setError] = React.useState("")
  const [working, setWorking] = React.useState(false)
  const input = React.useRef<HTMLInputElement>(null)
  const alive = React.useRef(true)
  React.useEffect(() => { setHandle(design.handleText ?? "") }, [design.handleText])
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
    setWorking(true); onBusy(true); setError("")
    try {
      const logoDataUrl = await prepareDesignLogo(file)
      if (!alive.current) return
      onChange(current => ({ ...current, logoDataUrl, logoVisible: true,
        cover: { ...current.cover, logoVisible: true }, inside: { ...current.inside, logoVisible: true },
      }))
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : "Could not open this logo.")
    } finally {
      if (alive.current) { setWorking(false); onBusy(false) }
    }
  }

  return <section className="simple-control-section simple-design-branding">
    <h2>Your branding</h2>
    <label className="design-field"><span>Handle</span><input aria-label="Design handle" value={handle} placeholder="@yourbrand" maxLength={31} autoCapitalize="none" autoCorrect="off" spellCheck={false}
      onChange={event => { setHandle(event.target.value); setError("") }} onBlur={saveHandle} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur() }} /></label>
    <div className="simple-logo-upload">
      <div className="simple-logo-upload-preview">{design.logoDataUrl ? <img src={design.logoDataUrl} alt="Logo for this design" /> : <Upload aria-hidden="true" />}</div>
      <div><Button size="sm" variant="secondary" disabled={working} onClick={() => input.current?.click()}><Upload className="size-3" />{working ? "Preparing…" : design.logoDataUrl ? "Replace logo" : "Upload logo"}</Button>
        <small>PNG, JPG or WebP · up to 5 MB</small></div>
      {design.logoDataUrl && <button type="button" aria-label="Remove design logo" disabled={working} onClick={() => onChange(current => ({ ...current, logoDataUrl: "" }))}><X className="size-3.5" /></button>}
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" aria-label="Upload design logo" className="sr-only" tabIndex={-1} onChange={pickLogo} disabled={working} />
    </div>
    {error && <p className="simple-branding-error" role="alert">{error}</p>}
    <p className="simple-control-help">Saved for this design. Blank fields use your connected account’s branding.</p>
  </section>
}
