/** Keep the background separate so the uploaded logo stays editable. */
export function DesignLogo({ src, background = "", className, label }: {
  src: string; background?: string; className?: string; label: string
}) {
  if (!background) return <img className={className} src={src} alt={label} draggable={false} />
  return <svg className={className} viewBox="0 0 512 512" role="img" aria-label={label}>
    <circle cx="256" cy="256" r="256" fill={background} />
    <image href={src} width="512" height="512" preserveAspectRatio="xMidYMid meet" />
  </svg>
}

export function LogoBackground({ value, onChange }: { value: string; onChange: (color: string) => void }) {
  const colors = [{ name: "White", color: "#ffffff" }, { name: "Black", color: "#000000" },
    { name: "Cream", color: "#f5eedf" }, { name: "Orange", color: "#c74726" },
    { name: "Blue", color: "#2563eb" }, { name: "Green", color: "#15803d" }]
  return <fieldset className="logo-background">
    <legend>Logo background</legend>
    <div className="logo-background-options">
      <button type="button" className="logo-background-clear" aria-pressed={!value} onClick={() => onChange("")}>Transparent</button>
      {colors.map(({ name, color }) => <button key={color} type="button" className="logo-background-swatch"
        aria-label={name + " logo background"} aria-pressed={value.toLowerCase() === color} title={name}
        style={{ background: color }} onClick={() => onChange(color)} />)}
      <label className="logo-background-custom">Custom<input type="color" aria-label="Custom logo background" value={value || "#ffffff"} onChange={event => onChange(event.target.value)} /></label>
    </div>
    <p className="simple-control-help">A solid circle behind your logo, on every slide.</p>
  </fieldset>
}
