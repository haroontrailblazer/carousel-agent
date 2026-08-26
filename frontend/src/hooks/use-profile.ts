import * as React from "react"

import { gravatarUrl } from "@/components/layout/user-avatar"
import { useAuth } from "@/hooks/use-auth"
import { supabase } from "@/lib/supabase"

export type Profile = {
  email: string
  /** Display name, or the email's local part when none is set. */
  displayName: string
  /** What the user actually typed, empty when unset. */
  name: string
  avatarUrl: string | null
}

/**
 * The signed-in person's name and picture.
 *
 * Held in Supabase Auth `user_metadata` rather than a table of our own: it is
 * already per-user, already authenticated, and already synchronised to every
 * tab through `onAuthStateChange` - so a rename shows up in the sidebar
 * without a refetch or a cache to invalidate.
 *
 * Gravatar is the fallback, requested with `d=404` so a missing one fails the
 * image load and the avatar falls back to an initial. A generic silhouette
 * would look like a picture the user had set.
 */
export function useProfile() {
  const { identity } = useAuth()
  const [name, setName] = React.useState("")
  const [avatarUrl, setAvatarUrl] = React.useState<string | null>(null)
  const [gravatar, setGravatar] = React.useState<string | null>(null)

  const read = React.useCallback(async () => {
    const { data } = await supabase.auth.getSession()
    const user = data.session?.user
    const meta = (user?.user_metadata ?? {}) as Record<string, unknown>
    const nextName = String(meta.username ?? meta.full_name ?? "")
    const nextAvatar = String(meta.avatar_url ?? "") || null
    setName(nextName)
    setAvatarUrl(nextAvatar)
    if (!nextAvatar && user?.email) {
      void gravatarUrl(user.email, 96).then(setGravatar)
    }
  }, [])

  React.useEffect(() => {
    void read()
    // Re-read on USER_UPDATED too, so saving a name updates the sidebar
    // immediately rather than only at the next sign-in.
    return supabase.auth.onAuthStateChange(() => void read())
  }, [read])

  const save = React.useCallback(
    async (next: { name?: string; avatarUrl?: string | null }) => {
      const payload: Record<string, unknown> = {}
      if (next.name !== undefined) payload.username = next.name.trim()
      if (next.avatarUrl !== undefined) payload.avatar_url = next.avatarUrl ?? ""
      const { error } = await supabase.auth.updateUser({ data: payload })
      if (error) throw new Error(error.message)
      await read()
    },
    [read],
  )

  const email = identity?.email ?? ""
  return {
    profile: {
      email,
      name,
      displayName: name || email.split("@")[0] || "Account",
      avatarUrl: avatarUrl || gravatar,
    } satisfies Profile,
    save,
    reload: read,
  }
}
