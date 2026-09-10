import * as React from "react"
import { Link } from "react-router"
import { Loader2, Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { type CarouselDesign, useCarouselDesigns } from "@/lib/designs"

export function NewsroomDesignDialog({ title, busy, onClose, onCreate, onReturnFocus }: {
  title: string
  busy: boolean
  onClose: () => void
  onCreate: (design: CarouselDesign) => void
  onReturnFocus: () => void
}) {
  const [designs, , syncStatus] = useCarouselDesigns()
  const [designId, setDesignId] = React.useState("")
  const selected = designs.find(design => design.id === designId)
  const loading = syncStatus === "loading"

  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="news-design-dialog" showClose={!busy} onCloseAutoFocus={event => {
      event.preventDefault()
      onReturnFocus()
    }}>
      <DialogHeader className="shrink-0 pr-12">
        <DialogTitle>Choose a design</DialogTitle>
        <DialogDescription>Set the style for your cover, slides, and final call to action.</DialogDescription>
      </DialogHeader>
      <div className="news-design-body">
        <p className="news-design-story" title={title}>{title}</p>
        {loading ? <p className="news-design-status" role="status"><Loader2 className="size-4 animate-spin" /> Loading your designs…</p> : <>
          {syncStatus === "offline" && <p className="news-design-status" role="status">Showing designs saved on this device. Your selection will be saved when you create.</p>}
          <fieldset className="news-design-options" disabled={busy}>
            <legend className="sr-only">Carousel design</legend>
            {designs.map(design => <label key={design.id} className="news-design-option">
              <input type="radio" name="newsroom-design" value={design.id} checked={designId === design.id} onChange={() => setDesignId(design.id)} />
              <span className="news-design-details">
                <span className="news-design-name">{design.name}</span>
                <span className="news-design-caption">Up to {design.maxSlides ?? 10} slides{design.handleText ? ` · ${design.handleText}` : ""}</span>
              </span>
              <span className="news-design-colors" aria-hidden="true">
                <i style={{ background: design.inside.background }} />
                <i style={{ background: design.inside.textColor }} />
                <i style={{ background: design.inside.accentColor }} />
              </span>
            </label>)}
          </fieldset>
          {designs.length === 0 && <p className="news-design-status">Create a design first, then come back to this story.</p>}
          {!busy && <Link to="/designs" className="news-design-edit" viewTransition>Edit your designs</Link>}
        </>}
      </div>
      <DialogFooter className="shrink-0">
        <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="brand" disabled={!selected || loading || busy} onClick={() => { if (selected && !busy) onCreate(selected) }}>
          {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
          {busy ? "Creating…" : "Create carousel"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
