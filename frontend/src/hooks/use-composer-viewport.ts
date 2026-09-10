import * as React from "react"

/** Keep the idle mobile composer in the visible viewport, including iOS panning. */
export function useComposerViewport(rootRef: React.RefObject<HTMLDivElement | null>, active: boolean) {
  React.useLayoutEffect(() => {
    const root = rootRef.current
    if (!active || !root) return
    const viewport = window.visualViewport
    const mobile = window.matchMedia("(max-width: 767px)")
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
    const animations = new Map<HTMLElement, Animation>()
    const motionElements = Array.from(root.querySelectorAll<HTMLElement>(
      ".studio-eyebrow, .studio-intro h1, .studio-description, .studio-hero-art, .agent-composer",
    ))
    let frame = 0
    let restingHeight = viewport?.height ?? window.innerHeight
    let restingWidth = window.innerWidth
    let keyboardSeen = false
    let previousHeight = viewport?.height ?? window.innerHeight
    let previousTop = viewport?.offsetTop ?? 0

    function stopMotion() {
      animations.forEach(animation => animation.cancel())
      animations.clear()
    }

    function update() {
      frame = 0
      if (!root) return
      if (!mobile.matches) {
        stopMotion()
        root.removeAttribute("data-editing")
        root.style.removeProperty("--new-visible-height")
        root.style.removeProperty("--new-visible-top")
        root.style.removeProperty("--new-menu-height")
        return
      }
      // Pinch zoom must retain normal browser panning rather than reflowing.
      if (viewport && Math.abs(viewport.scale - 1) > 0.05) return
      const height = viewport?.height ?? window.innerHeight
      const top = viewport?.offsetTop ?? 0
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
      const layoutChanged = editing !== wasEditing || Math.abs(height - previousHeight) > 1 || Math.abs(top - previousTop) > 1
      const animateLayout = root.hasAttribute("data-editing") && layoutChanged
        && (fieldFocused || wasEditing) && !reducedMotion.matches
      // Capture the currently painted positions before changing the layout.
      // Retarget from these positions if the keyboard resizes mid-animation.
      const before = animateLayout ? motionElements.map(element => ({ element, bounds: element.getBoundingClientRect() })) : []
      if (animateLayout || reducedMotion.matches) stopMotion()
      previousHeight = height
      previousTop = top
      root.dataset.editing = String(editing)
      root.style.setProperty("--new-visible-height", height + "px")
      root.style.setProperty("--new-visible-top", top + "px")
      const composer = root.querySelector<HTMLElement>(".agent-composer")
      const menuHeight = Math.max(40, height - (composer?.offsetHeight ?? 150) - 80)
      root.style.setProperty("--new-menu-height", menuHeight + "px")
      if (editing && composer) {
        // Scroll the field into view without removing the heading or artwork.
        // Native scrollIntoView can also pan the layout viewport on iOS.
        const bounds = root.getBoundingClientRect()
        const field = composer.getBoundingClientRect()
        // Measure the real destination, not the temporary animated position.
        const shift = animations.has(composer) ? new DOMMatrixReadOnly(getComputedStyle(composer).transform).m42 : 0
        const bottomOverflow = field.bottom - shift - (bounds.bottom - 16)
        if (bottomOverflow > 0) root.scrollTop += bottomOverflow
      } else if (wasEditing) {
        root.scrollTop = 0
      }
      for (const { element, bounds: start } of before) {
        const end = element.getBoundingClientRect()
        if (!start.width || !start.height || !end.width || !end.height) continue
        const x = start.left - end.left
        const y = start.top - end.top
        // Keep the live textarea at its normal scale while it moves.
        const scaleX = element === composer ? 1 : start.width / end.width
        const scaleY = element === composer ? 1 : start.height / end.height
        if (Math.abs(x) < .5 && Math.abs(y) < .5 && Math.abs(scaleX - 1) < .01 && Math.abs(scaleY - 1) < .01) continue
        const animation = element.animate([
          { transformOrigin: "top left", transform: `translate(${x}px, ${y}px) scale(${scaleX}, ${scaleY})` },
          { transformOrigin: "top left", transform: "translate(0, 0) scale(1, 1)" },
        ], { duration: editing !== wasEditing ? 340 : 220, easing: "cubic-bezier(.22, 1, .36, 1)" })
        animations.set(element, animation)
        animation.onfinish = () => { if (animations.get(element) === animation) animations.delete(element) }
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
    reducedMotion.addEventListener("change", schedule)
    root.addEventListener("focusin", schedule)
    root.addEventListener("focusout", schedule)
    update()
    return () => {
      cancelAnimationFrame(frame)
      stopMotion()
      observer.disconnect()
      viewport?.removeEventListener("resize", schedule)
      viewport?.removeEventListener("scroll", schedule)
      window.removeEventListener("resize", schedule)
      mobile.removeEventListener("change", schedule)
      reducedMotion.removeEventListener("change", schedule)
      root.removeEventListener("focusin", schedule)
      root.removeEventListener("focusout", schedule)
      root.removeAttribute("data-editing")
      for (const name of ["--new-visible-height", "--new-visible-top", "--new-menu-height"]) root.style.removeProperty(name)
    }
  }, [rootRef, active])
}
