import * as React from "react"
import { PanelLeft } from "lucide-react"
import { useLocation } from "react-router"

import { SidebarContent, SidebarDrawer } from "@/components/layout/sidebar"
import { Button } from "@/components/ui/button"

/**
 * The signed-in layout: a fixed sidebar on desktop, a drawer on small screens.
 *
 * A sidebar rather than a top bar because the console has a persistent piece
 * of state worth keeping in view - whether anything is waiting on a human. A
 * task at review blocks the whole pipeline until someone decides, so that
 * signal belongs somewhere permanent rather than behind a click. It is a dot,
 * not a number: the count changes nothing about what you do next.
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
        {/* No top bar on small screens - just the control that opens the
            drawer, floating over the page. The bar was spending 56px of a
            phone's height restating the app's name and mark, which the drawer
            it opens already shows. Fixed rather than sticky so it stays
            reachable once the page is scrolled. */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setMenuOpen(true)}
          className="fixed left-2 top-2 z-30 bg-[var(--background)]/80 backdrop-blur md:hidden"
        >
          <PanelLeft className="size-4" />
          <span className="sr-only">Open menu</span>
        </Button>

        <main
          className={
            isAgentWorkspace
              ? "agent-main"
              : "mx-auto max-w-5xl px-4 pb-8 pt-14 md:px-8 md:py-8"
          }
        >
          {children}
        </main>
      </div>
    </div>
  )
}
