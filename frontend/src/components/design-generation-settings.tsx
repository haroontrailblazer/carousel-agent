import * as React from "react"
import type { CarouselDesign } from "@/lib/designs"

export function DesignGenerationSettings({ design, onChange }: {
  design: CarouselDesign
  onChange: (change: (design: CarouselDesign) => CarouselDesign) => void
}) {
  const id = React.useId()
  const limit = design.maxSlides ?? 10
  return <section className="simple-control-section">
    <h2>Carousel length <span>For this design</span></h2>
    <label className="design-field" htmlFor={id}>
      <span>Maximum slides</span>
      <select id={id} value={limit} aria-describedby={`${id}-help`}
        onChange={event => {
          const maxSlides = Number(event.target.value)
          onChange(current => ({ ...current, maxSlides }))
        }}>
        {Array.from({ length: 8 }, (_, index) => index + 3).map(count =>
          <option key={count} value={count}>{count} slides</option>,
        )}
      </select>
    </label>
    <p id={`${id}-help`} className="simple-control-help">
      Includes the cover and final call-to-action slide, leaving up to {limit - 2} content {limit === 3 ? "slide" : "slides"}. Shorter stories can use fewer slides.
    </p>
  </section>
}
