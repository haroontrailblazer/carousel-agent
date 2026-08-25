import * as React from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { NavLink, useLocation } from "react-router"
import {
  Layers,
  LogOut,
  Moon,
  Newspaper,
  PanelLeftClose,
  Sparkles,
  Sun,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { BrandLogo } from "@/components/layout/brand-logo"
import { useAuth } from "@/hooks/use-auth"
import { get } from "@/lib/api"
import { useRuns } from "@/routes/history"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/new", label: "New carousel", icon: Sparkles, end: true },
  { to: "/newsroom", label: "Newsroom", icon: Newspaper, end: true },
  { to: "/tasks", label: "Tasks", icon: Layers, end: false },
] as const

function useTheme() {
  const [dark, setDark] = React.useState(() =>
    document.documentElement.classList.contains("dark"),
  )
  const toggle = React.useCallback(() => {
    setDark((previous) => {
      const next = !previous
      document.documentElement.classList.toggle("dark", next)
      try {
        localStorage.setItem("carousel-theme", next ? "dark" : "light")
      } catch {
        /* blocked storage: the class is what matters for this session */
      }
      return next
    })
  }, [])
  return { dark, toggle }
}

function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <BrandLogo className="size-9" />
      {!compact && (
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold leading-tight">
            Carousel Factory
          </span>
          <span className="block truncate text-xs text-[var(--muted-foreground)]">
            News to Instagram
          </span>
        </span>
      )}
    </span>
  )
}

/**
 * The count of runs waiting on a human.
 *
 * This is the one number worth putting in permanent view: a run sitting at
 * review is blocked on a person, and nothing else in the pipeline moves until
 * someone decides. Everything else can wait for the Runs screen.
 */
function ReviewBadge() {
  // useRuns() is the SAME cache entry the Tasks page uses, so this badge costs
  // no request of its own. It previously fetched its own filtered list on
  // every page, which meant two round trips to a remote database before
  // anything rendered.
  const runs = useRuns()
  const count = (runs.data?.items ?? []).filter(
    (r) => r.status === "awaiting_review",
  ).length
  if (!count) return null
  return (
    <span
      className="ml-auto inline-flex min-w-5 items-center justify-center rounded-[var(--radius-pill)] px-1.5 py-0.5 text-[11px] font-semibold leading-none"
      style={{
        background: "var(--phase-review-soft)",
        color: "var(--phase-review-fg)",
      }}
      title={`${count} task(s) waiting for your review`}
    >
      {count}
    </span>
  )
}


/** How many fetched stories are waiting to be picked. */
function QueueBadge() {
  const queue = useQuery({
    queryKey: ["queue"],
    queryFn: () => get<{ items: unknown[] }>("/api/queue"),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 120_000,
  })
  const count = queue.data?.items.length ?? 0
  if (!count) return null
  return (
    <span
      className="ml-auto inline-flex min-w-5 items-center justify-center rounded-[var(--radius-pill)] px-1.5 py-0.5 text-[11px] font-semibold leading-none"
      style={{ background: "var(--muted)", color: "var(--muted-foreground)" }}
      title={`${count} story(ies) waiting in the newsroom`}
    >
      {count}
    </span>
  )
}

export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { identity, signOut } = useAuth()
  const { dark, toggle } = useTheme()

  return (
    <div className="flex h-full flex-col gap-1 p-3">
      <div className="px-2 py-3">
        <BrandMark />
      </div>

      <nav className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-[var(--muted)] text-[var(--foreground)]"
                  : "text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]",
              )
            }
          >
            <Icon className="size-4 shrink-0" />
            <span className="truncate">{label}</span>
            {to === "/tasks" && <ReviewBadge />}
          {to === "/newsroom" && <QueueBadge />}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto space-y-1 border-t border-[var(--border)] pt-3">
        <button
          type="button"
          onClick={toggle}
          className="flex w-full items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-2 text-sm text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
        >
          {dark ? <Sun className="size-4 shrink-0" /> : <Moon className="size-4 shrink-0" />}
          <span className="truncate">{dark ? "Light mode" : "Dark mode"}</span>
        </button>

        <div className="flex items-center gap-2 rounded-[var(--radius-md)] px-2.5 py-2">
          <span
            aria-hidden
            className="grid size-7 shrink-0 place-items-center rounded-full bg-[var(--muted)] text-[11px] font-semibold uppercase"
          >
            {(identity?.email ?? "?").slice(0, 2)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs font-medium">
              {identity?.email}
            </span>
            <span className="block truncate text-[11px] text-[var(--muted-foreground)]">
              {identity?.role}
            </span>
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            onClick={() => void signOut()}
            title="Sign out"
          >
            <LogOut className="size-3.5" />
            <span className="sr-only">Sign out</span>
          </Button>
        </div>
      </div>
    </div>
  )
}

/** Mobile drawer. Rendered only when open so it cannot trap focus when hidden. */
export function SidebarDrawer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const location = useLocation()

  // Close on navigation - otherwise tapping a link on a phone leaves the
  // drawer covering the page you just asked for.
  React.useEffect(() => {
    onClose()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname])

  React.useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <button
        type="button"
        aria-label="Close menu"
        onClick={onClose}
        className="absolute inset-0 bg-black/40"
      />
      <div className="absolute inset-y-0 left-0 w-64 border-r border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-lift)]">
        <div className="flex justify-end p-2">
          <Button variant="ghost" size="icon" onClick={onClose}>
            <PanelLeftClose className="size-4" />
            <span className="sr-only">Close menu</span>
          </Button>
        </div>
        <SidebarContent onNavigate={onClose} />
      </div>
    </div>
  )
}
