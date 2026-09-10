import * as React from "react"

/** Keep the idle mobile composer in the visible viewport, including iOS panning. */
export function useComposerViewport(rootRef: React.RefObject<HTMLDivElement | null>, active: boolean) {
  React.useLayoutEffect(() => {
    const root = rootRef.current
    if (!active || !root) return
    const viewport = window.visualViewport
    const mobile = window.matchMedia("(max-width: 767px)")
    let frame = 0
    let restingHeight = viewport?.height ?? window.innerHeight
    let restingWidth = window.innerWidth
    let keyboardSeen = false

    function update() {
      frame = 0
      if (!root) return
      if (!mobile.matches) {
        root.removeAttribute("data-editing")
        root.style.removeProperty("--new-visible-height")
        root.style.removeProperty("--new-visible-top")
        root.style.removeProperty("--new-menu-height")
        return
      }
      // Pinch zoom must retain normal browser panning rather than reflowing.
      if (viewport && Math.abs(viewport.scale - 1) > 0.05) return
      const height = viewport?.height ?? window.innerHeight
      const focused = document.activeElement
      const fieldFocused = focused instanceof HTMLElement
        && root.contains(focused) && focused.matches("textarea, input")
      if (!fieldFocused || Math.abs(window.innerWidth - restingWidth) > 50) {
        restingHeight = Math.max(height, window.innerHeight)
        restingWidth = window.innerWidth
        keyboardSeen = false
      }
      const coveredHeight = restingHeight - height
      if (fieldFocused && coveredHeight > 100) keyboardSeen = true
      // Android can dismiss the keyboard while leaving the textarea focused.
      const editing = fieldFocused && (!keyboardSeen || coveredHeight > 80)
      const wasEditing = root.dataset.editing === "true"
      root.dataset.editing = String(editing)
      root.style.setProperty("--new-visible-height", height + "px")
      root.style.setProperty("--new-visible-top", (viewport?.offsetTop ?? 0) + "px")
      const composer = root.querySelector<HTMLElement>(".agent-composer")
      const menuHeight = Math.max(40, height - (composer?.offsetHeight ?? 150) - 80)
      root.style.setProperty("--new-menu-height", menuHeight + "px")
      if (editing && composer) {
        // Scroll the field into view without removing the heading or artwork.
        // Native scrollIntoView can also pan the layout viewport on iOS.
        const bounds = root.getBoundingClientRect()
        const field = composer.getBoundingClientRect()
        const bottomOverflow = field.bottom - (bounds.bottom - 16)
        if (bottomOverflow > 0) root.scrollTop += bottomOverflow
      } else if (wasEditing) {
        root.scrollTop = 0
      }
    }
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update)
    }
    const observer = new ResizeObserver(schedule)
    const composer = root.querySelector(".agent-composer")
    if (composer) observer.observe(composer)
    viewport?.addEventListener("resize", schedule)
    viewport?.addEventListener("scroll", schedule)
    window.addEventListener("resize", schedule)
    mobile.addEventListener("change", schedule)
    root.addEventListener("focusin", schedule)
    root.addEventListener("focusout", schedule)
    update()
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      viewport?.removeEventListener("resize", schedule)
      viewport?.removeEventListener("scroll", schedule)
      window.removeEventListener("resize", schedule)
      mobile.removeEventListener("change", schedule)
      root.removeEventListener("focusin", schedule)
      root.removeEventListener("focusout", schedule)
      root.removeAttribute("data-editing")
      for (const name of ["--new-visible-height", "--new-visible-top", "--new-menu-height"]) root.style.removeProperty(name)
    }
  }, [rootRef, active])
}
