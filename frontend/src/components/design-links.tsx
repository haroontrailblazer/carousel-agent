import * as React from "react"
import { Link2 } from "lucide-react"
import type { CarouselDesign } from "@/lib/designs"

function DestinationField({ label, value, placeholder, onSave }: {
  label: string
  value: string
  placeholder: string
  onSave: (value: string) => void
}) {
  const [draft, setDraft] = React.useState(value)
  const [error, setError] = React.useState("")
  const id = React.useId()
  React.useEffect(() => { setDraft(value); setError("") }, [value])

  function save() {
    let text = draft.trim()
    if (text) {
      try {
        const url = new URL(text)
        if (!/^https?:$/.test(url.protocol) || !url.hostname || url.username || url.password || /\s/.test(text)) throw new Error()
        text = url.href
      } catch {
        setError("Enter a full http:// or https:// link without spaces or login details.")
        return
      }
    }
    setError("")
    setDraft(text)
    if (text !== value) onSave(text)
  }

  return <div className="branding-destination">
    <label className="design-field branding-handle" htmlFor={id}>
      <span>{label} URL <small>Optional</small></span>
      <input id={id} type="url" inputMode="url" value={draft} placeholder={placeholder}
        maxLength={2048} autoCapitalize="none" autoCorrect="off" spellCheck={false}
        aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined}
        onChange={event => { setDraft(event.target.value); setError("") }}
        onBlur={save} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur() }} />
    </label>
    {error && <p id={`${id}-error`} className="simple-branding-error" role="alert">{error}</p>}
  </div>
}

export function DesignLinks({ design, onChange }: {
  design: CarouselDesign
  onChange: (change: (design: CarouselDesign) => CarouselDesign) => void
}) {
  return <div className="branding-destinations">
    <h3><Link2 aria-hidden="true" /> Call-to-action links</h3>
    <p className="simple-control-help">Choose where this design's closing slide sends readers. Links save with this design.</p>
    <DestinationField label="Substack" value={design.substackUrl ?? ""} placeholder="https://yourname.substack.com"
      onSave={substackUrl => onChange(current => ({ ...current, substackUrl }))} />
    <DestinationField label="YouTube" value={design.youtubeUrl ?? ""} placeholder="https://youtube.com/@yourchannel"
      onSave={youtubeUrl => onChange(current => ({ ...current, youtubeUrl }))} />
    <p className="simple-control-help">Leave both empty to use a follow or comment call to action.</p>
  </div>
}
