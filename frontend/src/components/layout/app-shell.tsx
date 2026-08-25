import * as React from "react"
import { Link, NavLink } from "react-router"
import { LogOut, Moon, Sun, Wrench } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useAuth } from "@/hooks/use-auth"
import { cn } from "@/lib/utils"

function useTheme() {
  const [dark, setDark] = React.useState(
    () => document.documentElement.classList.contains("dark"),
  )
  const toggle = React.useCallback(() => {
    setDark((previous) => {
      const next = !previous
      document.documentElement.classList.toggle("dark", next)
      try {
        localStorage.setItem("carousel-theme", next ? "dark" : "light")
      } catch {
        /* storage can be blocked; the class is what actually matters */
      }
      return next
    })
  }, [])
  return { dark, toggle }
}

/** The lime mark - the one place the brand colour is used as a large fill. */
function BrandMark() {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className="grid size-7 place-items-center rounded-[9px] font-bold text-[13px]"
        style={{ background: "var(--brand)", color: "var(--brand-foreground)" }}
      >
        CF
      </span>
      <span className="font-semibold tracking-tight">Carousel Factory</span>
    </span>
  )
}

const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-[var(--radius-pill)] px-3 py-1.5 text-sm font-medium transition-colors",
    isActive
      ? "bg-[var(--muted)] text-[var(--foreground)]"
      : "text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]",
  )

export function AppShell({ children }: { children: React.ReactNode }) {
  const { identity, signOut } = useAuth()
  const { dark, toggle } = useTheme()

  return (
    <div className="min-h-dvh bg-[var(--background)]">
      <header className="sticky top-0 z-30 border-b border-[var(--border)] bg-[var(--background)]/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <Link to="/new" className="shrink-0">
            <BrandMark />
          </Link>

          <nav className="flex items-center gap-1">
            <NavLink to="/new" className={navLink}>
              New
            </NavLink>
            <NavLink to="/runs" className={navLink}>
              Runs
            </NavLink>
          </nav>

          <div className="ml-auto flex items-center gap-1">
            {/* The ADK dev UI. Kept one click away because when a run
                misbehaves, the agent-by-agent event trace is the fastest way
                to find out why. */}
            <Button variant="ghost" size="icon" asChild title="ADK dev UI">
              <a href="/dev" target="_blank" rel="noreferrer">
                <Wrench />
                <span className="sr-only">ADK dev UI</span>
              </a>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggle}
              title={dark ? "Switch to light" : "Switch to dark"}
            >
              {dark ? <Sun /> : <Moon />}
              <span className="sr-only">Toggle theme</span>
            </Button>
            <span className="hidden text-sm text-[var(--muted-foreground)] sm:inline">
              {identity?.email}
            </span>
            <Button variant="ghost" size="icon" onClick={() => void signOut()} title="Sign out">
              <LogOut />
              <span className="sr-only">Sign out</span>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  )
}
