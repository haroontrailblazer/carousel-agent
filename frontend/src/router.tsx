import { createBrowserRouter, Navigate, Outlet, useLocation } from "react-router"

import { AppShell } from "@/components/layout/app-shell"
import { useAuth } from "@/hooks/use-auth"
import { HistoryRoute } from "@/routes/history"
import { LoginRoute } from "@/routes/login"
import { NewRunRoute } from "@/routes/new-run"
import { NotFoundRoute } from "@/routes/not-found"
import { ResetPasswordRoute } from "@/routes/reset-password"
import { ReviewRoute } from "@/routes/review"
import { RunDetailRoute } from "@/routes/run-detail"

/**
 * Guard every screen behind a signed-in session.
 *
 * A layout route rather than a check inside each page: one place to get right,
 * and a new route is protected by where it sits in the tree rather than by
 * someone remembering to add a line.
 */
function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === "pending") {
    // Not the login screen. Rendering it here would flash sign-in on every
    // reload before the existing session finishes loading.
    return (
      <div className="grid min-h-dvh place-items-center">
        <div
          className="size-6 rounded-full border-2 border-[var(--border)] border-t-[var(--brand)] animate-spin-slow"
          aria-label="Loading"
        />
      </div>
    )
  }
  if (status === "out") {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}

export const router = createBrowserRouter([
  // PUBLIC. Every landing page named in an auth email must be listed here -
  // Oreag's proxy.ts documents what happens otherwise: the links in every
  // password-reset mail dead-end on the sign-in screen.
  { path: "/login", element: <LoginRoute /> },
  { path: "/reset-password", element: <ResetPasswordRoute /> },

  {
    element: <RequireAuth />,
    children: [
      { path: "/", element: <Navigate to="/new" replace /> },
      { path: "/new", element: <NewRunRoute /> },
      { path: "/runs", element: <HistoryRoute /> },
      { path: "/runs/:runId", element: <RunDetailRoute /> },
      { path: "/runs/:runId/review", element: <ReviewRoute /> },
    ],
  },
  { path: "*", element: <NotFoundRoute /> },
])
