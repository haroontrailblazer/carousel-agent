import * as React from "react"
import { Navigate, useLocation, useNavigate } from "react-router"

import { Button } from "@/components/ui/button"
import { BrandLogo } from "@/components/layout/brand-logo"
import { StudioArtwork } from "@/components/layout/studio-artwork"
import { Input, Label } from "@/components/ui/input"
import { useAuth } from "@/hooks/use-auth"
import { loadAuthConfig, supabase } from "@/lib/supabase"

/**
 * GoTrue's raw messages are not always the truth a user needs.
 *
 * "Invalid login credentials" is returned for a wrong password AND for an
 * unconfirmed email, and rate limiting arrives as an opaque 429. Mapping them
 * here is the difference between a user retrying usefully and giving up.
 */
function authMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? "")
  const lower = raw.toLowerCase()
  if (lower.includes("invalid login credentials")) {
    return "That email and password do not match an account."
  }
  if (lower.includes("email not confirmed")) {
    return "That account still needs its email confirmed."
  }
  if (lower.includes("rate") || lower.includes("too many")) {
    return "Too many attempts. Wait a minute and try again."
  }
  if (lower.includes("not on the access list")) {
    return raw // the server's message is already specific and correct
  }
  return raw || "Could not sign in."
}

/**
 * Where to go once signed in.
 *
 * Reads `?next=` first: that is what the SERVER sets when it turns away an
 * unauthenticated HTML request, and it is the only channel that survives a
 * cold navigation from outside the app - a Telegram review link, a bookmark,
 * a pasted URL. React Router's `location.state` cannot carry those, because
 * there was no in-app navigation to attach state to.
 *
 * Only path-absolute, same-origin destinations are honoured. `//evil.com` and
 * `https://evil.com` are both rejected: an open redirect on a login page is a
 * phishing primitive - it lets an attacker send a real link to the real site
 * that deposits the user somewhere else immediately after they type a
 * password.
 */
function redirectTarget(search: string, stateFrom?: string): string {
  const candidate = new URLSearchParams(search).get("next") ?? stateFrom
  if (!candidate) return "/new"
  if (!candidate.startsWith("/") || candidate.startsWith("//")) return "/new"
  return candidate
}

export function LoginRoute() {
  const { status, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [error, setError] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const [signup, setSignup] = React.useState(false)
  const [notice, setNotice] = React.useState("")
  const [configured, setConfigured] = React.useState<boolean | null>(null)

  React.useEffect(() => {
    void loadAuthConfig().then((c) => setConfigured(c.configured))
  }, [])

  // A signed-in user landing here (via Back, or a stale bookmark) should not
  // see a login form.
  if (status === "in") {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={redirectTarget(location.search, from)} replace />
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError("")
    setNotice("")
    try {
      if (signup) {
        const { data, error } = await supabase.auth.signUp({ email: email.trim(), password,
          options: { emailRedirectTo: window.location.origin + "/login" } })
        if (error) throw error
        if (!data.session) {
          setNotice("Check your email to confirm your account, then sign in here.")
          setSignup(false)
          setPassword("")
          return
        }
      }
      await signIn(email.trim(), password)
      const from = (location.state as { from?: string } | null)?.from
      navigate(redirectTarget(location.search, from), { replace: true })
    } catch (err) {
      setError(authMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="studio-login">
      <section className="studio-login-story" aria-label="Carousel Factory">
        <div className="studio-login-wordmark"><BrandLogo className="size-11" /><span>Carousel Factory</span></div>
        <div className="studio-login-headline">
          <p className="studio-eyebrow">Your agent-powered studio</p>
          <h2>From the first<br />idea to the<br /><em>final swipe.</em></h2>
          <StudioArtwork name="carousel-sculpture" className="studio-login-art" sizes="(max-width: 767px) 0px, 240px" />
          <p>A creative team for every story. Research, copy, design, and quality checks — brought together in one workspace.</p>
        </div>
        <p className="studio-login-footer">Made by agents. Directed by you.</p>
      </section>
      <div className="studio-login-form">
      <div>
        <h1>{signup ? "Your own creative studio." : "Welcome to your studio."}</h1>
        <p className="studio-login-subtitle">{signup ? "Create an account. Your designs, media and settings stay yours." : "Sign in to create something worth sharing."}</p>

        {configured === false && (
          <p className="mb-4 rounded-[var(--radius-md)] bg-[var(--phase-failed-soft)] px-3 py-2 text-sm"
             style={{ color: "var(--phase-failed-fg)" }}>
            Sign-in is not configured on the server yet. Set SUPABASE_URL and
            SUPABASE_ANON_KEY, then reload.
          </p>
        )}

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete={signup ? "new-password" : "current-password"}
              minLength={signup ? 8 : undefined}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {notice && <p role="status" className="text-sm text-[var(--muted-foreground)]">{notice}</p>}
          {error && (
            <p
              className="rounded-[var(--radius-md)] px-3 py-2 text-sm"
              style={{
                background: "var(--phase-failed-soft)",
                color: "var(--phase-failed-fg)",
              }}
              role="alert"
            >
              {error}
            </p>
          )}

          <Button
            type="submit"
            variant="brand"
            className="w-full"
            disabled={busy || configured === false}
          >
            {busy ? (signup ? "Creating account…" : "Signing in…") : signup ? "Create account" : "Sign in"}
          </Button>
        </form>

        <p className="mt-5 text-center text-xs text-[var(--muted-foreground)]">
          {signup ? "Already have an account? " : "New here? "}
          <button type="button" className="underline underline-offset-4" disabled={busy} onClick={() => { setSignup(!signup); setError(""); setNotice("") }}>{signup ? "Sign in" : "Create your account"}</button>
        </p>
      </div>
      </div>
    </div>
  )
}
