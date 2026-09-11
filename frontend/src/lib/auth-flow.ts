import { getSupabase, loadAuthConfig } from "./supabase"

export type AuthCapabilities = { providers: ("google" | "github")[]; passkeys: boolean }
export async function authCapabilities(): Promise<AuthCapabilities> {
  const config = await loadAuthConfig()
  if (!config.configured) throw new Error("Sign-in is temporarily unavailable. Please try again shortly.")
  const response = await fetch(config.supabase_url + "/auth/v1/settings", { headers: { apikey: config.supabase_anon_key } })
  if (!response.ok) throw new Error("Could not load sign-in options. Please try again.")
  const data = await response.json()
  return { providers: (["google", "github"] as const).filter(provider => data.external?.[provider] === true), passkeys: data.passkeys_enabled === true }
}

export function safeDestination(candidate: string | null | undefined): string {
  if (!candidate || !candidate.startsWith("/") || candidate.startsWith("//") || /[\\\u0000-\u0020]/.test(candidate)) return "/new"
  try {
    const destination = new URL(candidate, window.location.origin)
    if (destination.origin !== window.location.origin || /^\/(login|signup|forgot-password|reset-password|auth)(\/|$)/.test(destination.pathname)) return "/new"
    return destination.pathname + destination.search + destination.hash
  } catch { return "/new" }
}

export function authError(error: unknown): string {
  const message = error instanceof Error ? error.message : "Something went wrong. Please try again."
  const value = message.toLowerCase()
  if (value.includes("invalid login")) return "That email and password don’t match. Try again or use an email code."
  if (value.includes("email not confirmed")) return "Confirm your email first, or request a new confirmation code."
  if (value.includes("expired") || value.includes("invalid otp")) return "That code or link has expired. Request a new one below."
  if (value.includes("rate") || value.includes("too many")) return "Too many attempts. Please wait a moment before trying again."
  if (value.includes("email address not authorized")) return "Email delivery is temporarily unavailable. Please try again later."
  return message
}

export const passwordRules = [
  { label: "12 characters", test: (value: string) => value.length >= 12 },
  { label: "Uppercase", test: (value: string) => /[A-Z]/.test(value) },
  { label: "Lowercase", test: (value: string) => /[a-z]/.test(value) },
  { label: "Number", test: (value: string) => /[0-9]/.test(value) },
  { label: "Symbol", test: (value: string) => /[!@#$%^&*()_+\-=\[\]{};'\\:"|<>?,./`~]/.test(value) },
]
export const validPassword = (value: string) => passwordRules.every(rule => rule.test(value))

export async function activeSession() {
  const client = await getSupabase()
  const { data, error } = await client.auth.getSession()
  if (error) throw error
  if (!data.session) throw new Error("Your sign-in link has expired. Please request a new one.")
  return { client, session: data.session }
}
