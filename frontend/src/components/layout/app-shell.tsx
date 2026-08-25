import * as React from "react"
import { PanelLeft } from "lucide-react"
import { useLocation } from "react-router"

import { SidebarContent, SidebarDrawer } from "@/components/layout/sidebar"
import { BrandLogo } from "@/components/layout/brand-logo"
import { Button } from "@/components/ui/button"

/**
 * The signed-in layout: a fixed sidebar on desktop, a drawer on small screens.
 *
 * A sidebar rather than a top bar because the console has a persistent piece
 * of state worth keeping in view - how many runs are waiting on a human. A run
 * at review blocks the whole pipeline until someone decides, so that count
 * belongs somewhere permanent rather than behind a click.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = React.useState(false)
  const location = useLocation()
  const isAgentWorkspace = location.pathname === "/new"

  return (
    <div className="min-h-dvh bg-[var(--background)]">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[17rem] border-r border-[var(--border)] bg-[var(--card)] md:block">
        <SidebarContent />
      </aside>

      <SidebarDrawer open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="md:pl-[17rem]">
        {/* Slim bar that exists only to hold the menu button on small screens. */}
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-[var(--border)] bg-[var(--background)]/85 px-4 backdrop-blur md:hidden">
          <Button variant="ghost" size="icon" onClick={() => setMenuOpen(true)}>
            <PanelLeft className="size-4" />
            <span className="sr-only">Open menu</span>
          </Button>
          <BrandLogo className="size-8" />
          <span className="text-sm font-semibold">Carousel Factory</span>
        </header>

        <main
          className={
            isAgentWorkspace
              ? "agent-main"
              : "mx-auto max-w-5xl px-4 py-8 md:px-8"
          }
        >
          {children}
        </main>
      </div>
    </div>
  )
}
