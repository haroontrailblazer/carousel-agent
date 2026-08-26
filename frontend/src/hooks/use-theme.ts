import * as React from "react"

const STORAGE_KEY = "carousel-theme"

/**
 * Light / dark for this browser.
 *
 * The class on <html> is the source of truth, set before first paint by the
 * inline script in index.html - so reading it here is reading what is already
 * on screen, with no flash and no second opinion.
 *
 * Lifted out of the sidebar when the profile page gained a proper appearance
 * control: two components driving the same setting from two private copies of
 * this state would disagree the moment either one changed it.
 */
export function useTheme() {
  const [dark, setDarkState] = React.useState(() =>
    document.documentElement.classList.contains("dark"),
  )

  const setDark = React.useCallback((next: boolean) => {
    document.documentElement.classList.toggle("dark", next)
    try {
      localStorage.setItem(STORAGE_KEY, next ? "dark" : "light")
    } catch {
      /* blocked storage: the class is what matters for this session */
    }
    setDarkState(next)
  }, [])

  // Another surface (the sidebar, the profile page) may have changed it while
  // this component was mounted; re-read when the DOM says so.
  React.useEffect(() => {
    const observer = new MutationObserver(() =>
      setDarkState(document.documentElement.classList.contains("dark")),
    )
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    })
    return () => observer.disconnect()
  }, [])

  const toggle = React.useCallback(() => setDark(!dark), [dark, setDark])
  return { dark, setDark, toggle }
}
