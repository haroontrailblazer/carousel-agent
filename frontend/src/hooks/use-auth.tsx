/**
 * Who is signed in, and the sign-in / sign-out actions.
 *
 * The flow is deliberately split across two systems:
 *
 *  1. supabase-js does the actual sign-in in the browser. Passwords never
 *     touch our server.
 *  2. The resulting access token is posted ONCE to /api/auth/session, which
 *     verifies it, provisions the private workspace, and sets an httpOnly cookie. Every
 *     request after that rides the cookie.
 *
 * The cookie exists because our SPA is not the only thing that needs to
 * authenticate: the ADK dev UI at /dev issues its own requests we cannot add
 * headers to, and EventSource cannot set headers at all.
 *
 * `status` is three-valued on purpose. Collapsing "pending" into "signed out"
 * makes the app flash the login screen on every reload while the session is
 * still being read.
 */

import * as React from "react"
import { useQueryClient } from "@tanstack/react-query"

import { onSessionExpired, probe } from "@/lib/api"
import { setWorkspaceScope } from "@/lib/workspace"
import { supabase } from "@/lib/supabase"
import type { Identity } from "@/lib/types"

type AuthStatus = "pending" | "in" | "out"

/**
 * Whether this BROWSER has ever completed a sign-in - not whether the session
 * is still valid, which only the server can say.
 *
 * Confirming a session is a round trip, and until it answers the app has to
 * render something. What it should render depends entirely on which way that
 * answer is likely to go, and this is the only evidence available before it
 * arrives. A returning user gets the console's own layout, drawn empty; a
 * first-time visitor gets a plain spinner, because showing them a console
 * they are about to be redirected away from would be worse than showing them
 * nothing.
 *
 * A hint, never a decision. Nothing is unlocked by it and nothing is shown
 * because of it that was not going to be public anyway - it chooses between
 * two loading screens. Setting this key by hand buys you a different
 * placeholder for 300ms.
 */
const SESSION_HINT_KEY = "carousel-had-session"

export function hadSession(): boolean {
  try {
    return localStorage.getItem(SESSION_HINT_KEY) === "1"
  } catch {
    // Private mode or blocked storage: fall back to the spinner.
    return false
  }
}

function rememberSession(known: boolean): void {
  try {
    if (known) localStorage.setItem(SESSION_HINT_KEY, "1")
    else localStorage.removeItem(SESSION_HINT_KEY)
  } catch {
    /* the app works without it; only the placeholder changes */
  }
}

type AuthValue = {
  status: AuthStatus
  identity: Identity | null
  signIn: (email: string, password: string) => Promise<void>
  completeSignIn: (token: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = React.createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const account = React.useRef("")
  const revision = React.useRef(0)
  const [status, setStatus] = React.useState<AuthStatus>("pending")
  const [identity, setIdentity] = React.useState<Identity | null>(null)

  const refresh = React.useCallback(async () => {
    // probe() rather than get(): asking whether someone is signed in must not
    // itself cause a navigation. Routing this through api() meant a 401 here
    // triggered a redirect to /login - and on /login that redirect fired
    // again on every mount, reloading the page forever.
    const request = ++revision.current
    const me = await probe<Identity>("/api/auth/me")
    if (request !== revision.current) return
    const owner = me?.id ?? me?.email ?? ""
    if (account.current !== owner) {
      await queryClient.cancelQueries()
      if (request !== revision.current) return
      queryClient.clear()
      account.current = owner
      setWorkspaceScope(owner)
    }
    if (me) {
      setIdentity(me)
      setStatus("in")
      rememberSession(true)
    } else {
      setIdentity(null)
      setStatus("out")
      // Clear it here rather than only on an explicit sign-out: an expired
      // session must stop this browser claiming to be a returning user, or it
      // gets the console's layout on every load right up to the redirect.
      rememberSession(false)
    }
  }, [queryClient])

  React.useEffect(() => {
    void refresh()
    // If any request anywhere discovers the session is dead, drop it here too
    // so the UI stops rendering signed-in chrome behind the redirect.
    return onSessionExpired(() => {
      revision.current++
      setIdentity(null)
      setStatus("out")
      rememberSession(false)
      account.current = ""
      setWorkspaceScope("")
      void queryClient.cancelQueries().then(() => queryClient.clear())
    })
  }, [refresh])

  React.useEffect(() => {
    const update = () => { if (document.visibilityState === "visible") void refresh() }
    window.addEventListener("focus", update)
    window.addEventListener("pageshow", update)
    return () => { window.removeEventListener("focus", update); window.removeEventListener("pageshow", update) }
  }, [refresh])

  const completeSignIn = React.useCallback(async (token: string) => {
    const response = await fetch("/api/auth/session", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: token }),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw Object.assign(new Error(body.error ?? "Could not start a session."), { code: body.code })
    }
    await refresh()
  }, [refresh])

  const signIn = React.useCallback(async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
    if (!data.session?.access_token) throw new Error("Sign-in did not return a session.")
    await completeSignIn(data.session.access_token)
  }, [completeSignIn])

  const signOut = React.useCallback(async () => {
    // Do not claim sign-out succeeded while an httpOnly cookie is still active.
    const response = await fetch("/api/auth/session", { method: "DELETE", credentials: "include" })
    if (!response.ok) throw new Error("Could not sign out. Please check your connection and try again.")
    revision.current++
    setIdentity(null)
    setStatus("out")
    await queryClient.cancelQueries()
    queryClient.clear()
    account.current = ""
    setWorkspaceScope("")
    // scope: "local" - one expired tab must not sign the user out on their
    // other devices.
    await supabase.auth.signOut({ scope: "local" })
    setIdentity(null)
    setStatus("out")
    rememberSession(false)
  }, [queryClient])

  const value = React.useMemo(
    () => ({ status, identity, signIn, completeSignIn, signOut, refresh }),
    [status, identity, signIn, completeSignIn, signOut, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = React.useContext(AuthContext)
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>")
  return value
}
