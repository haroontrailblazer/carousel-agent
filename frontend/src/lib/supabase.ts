/**
 * The Supabase browser client.
 *
 * Configuration is fetched from the backend rather than baked in at build time
 * (`/api/auth/config`). That means one built bundle works against any
 * deployment, and rotating the project does not require a rebuild.
 *
 * The anon key being public is by design - it identifies the project and
 * authorises nothing on its own. The database is locked against it directly:
 * db/migrations/003_lockdown.sql revokes anon/authenticated on every table and
 * sets default privileges so tables ADK creates later are born locked too.
 *
 * Note there is no @supabase/ssr here and no server client. This is a static
 * bundle; the only server is FastAPI, and it verifies tokens itself.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js"

export type AuthConfig = {
  supabase_url: string
  supabase_anon_key: string
  configured: boolean
}

let client: SupabaseClient | null = null
let configPromise: Promise<AuthConfig> | null = null

export async function loadAuthConfig(): Promise<AuthConfig> {
  if (!configPromise) {
    configPromise = fetch("/api/auth/config", { credentials: "include" })
      .then((r) => r.json())
      .catch(() => ({ supabase_url: "", supabase_anon_key: "", configured: false }))
  }
  return configPromise
}

/**
 * Build (once) and return the Supabase client.
 *
 * Throws when the backend has no Supabase configuration, so the login screen
 * can say "this console is not configured yet" instead of failing with an
 * opaque network error from inside the SDK.
 */
export async function getSupabase(): Promise<SupabaseClient> {
  if (client) return client
  const config = await loadAuthConfig()
  if (!config.configured) {
    throw new Error(
      "Sign-in is not configured: set SUPABASE_URL and SUPABASE_ANON_KEY on the server.",
    )
  }
  client = createClient(config.supabase_url, config.supabase_anon_key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      // Needed for password-recovery links, which land with the token in the
      // URL fragment.
      detectSessionInUrl: true,
    },
  })
  return client
}

/**
 * A proxy so modules can `import { supabase }` without awaiting.
 *
 * Only the handful of auth methods this app uses are forwarded; everything
 * else would need the async getter anyway, and this app never queries Supabase
 * directly - all data comes from our own API.
 */
export const supabase = {
  auth: {
    async signOut(options?: { scope?: "global" | "local" | "others" }) {
      try {
        const c = await getSupabase()
        return await c.auth.signOut(options)
      } catch {
        return { error: null }
      }
    },
    async getSession() {
      const c = await getSupabase()
      return c.auth.getSession()
    },
    async signInWithPassword(credentials: { email: string; password: string }) {
      const c = await getSupabase()
      return c.auth.signInWithPassword(credentials)
    },
    async resetPasswordForEmail(email: string, options?: { redirectTo?: string }) {
      const c = await getSupabase()
      return c.auth.resetPasswordForEmail(email, options)
    },
    async updateUser(attributes: {
      password?: string
      /** user_metadata - where the display name and avatar live. */
      data?: Record<string, unknown>
    }) {
      const c = await getSupabase()
      return c.auth.updateUser(attributes)
    },
    /**
     * Subscribe to auth changes.
     *
     * Returns an unsubscribe function rather than Supabase's nested
     * `{data:{subscription}}`, because the client is fetched asynchronously -
     * callers cannot hold the subscription object at effect-setup time.
     * Unsubscribing before the client resolves is honoured.
     */
    onAuthStateChange(handler: () => void): () => void {
      let cancelled = false
      let unsubscribe: (() => void) | undefined
      void getSupabase()
        .then((c) => {
          if (cancelled) return
          const { data } = c.auth.onAuthStateChange(() => handler())
          unsubscribe = () => data.subscription.unsubscribe()
        })
        .catch(() => undefined)
      return () => {
        cancelled = true
        unsubscribe?.()
      }
    },
  },
}
