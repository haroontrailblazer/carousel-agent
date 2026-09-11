import * as React from "react"
import { StudioArtwork } from "@/components/layout/studio-artwork"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Cable,
  Activity,
  Check,
  CircleUserRound,
  ExternalLink,
  Instagram,
  KeyRound,
  Moon,
  Palette,
  Radio,
  Send,
  Sun,
  Trash2,
  Unplug,
  Upload,
} from "lucide-react"
import { toast } from "sonner"

import { UserAvatar } from "@/components/layout/user-avatar"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Chip, MutedChip } from "@/components/ui/chip"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { TabPanel, Tabs } from "@/components/ui/tabs"
import { useProfile } from "@/hooks/use-profile"
import { useTheme } from "@/hooks/use-theme"
import { ApiError, del, get, post, postBytes } from "@/lib/api"
import { compressAvatar } from "@/lib/image"
import "./profile.css"
import { AISettingsSection } from "./ai-settings"
import { TracingSettingsSection } from "./tracing-settings"

type InstagramAccount = {
  id: string
  username: string
  handle: string
  name: string
  avatar_key: string
  is_default: boolean
  disabled: boolean
  /** True when the token is expired, unreadable, or the account is disabled. */
  needs_reconnect: boolean
  expires_in_days: number | null
  connected_by: string
  connected_at: string
}

type InstagramStatus = {
  /** False when IG_APP_ID / IG_APP_SECRET are missing - nothing can connect. */
  app_configured: boolean
  public_base_url_set: boolean
  /** False when SECRETS_KEY is absent - no token can be stored safely. */
  secrets_ready: boolean
  redirect_uri: string
  accounts: InstagramAccount[]
}

/** What POST /settings/instagram/token answers with. Never carries a token. */
type InstagramConnectResponse = InstagramStatus & {
  result: string
  account: InstagramAccount
  /**
   * "confirmed" when Meta refreshed the pasted token and stated the real
   * expiry; "assumed" when it would not, and 60 days had to be taken on faith.
   */
  expiry: "confirmed" | "assumed"
}

type TelegramBot = {
  bot_id: string
  bot_username: string
  chat_id: string
  token_masked: string
  connected: boolean
}

type TelegramStatus = {
  bots: TelegramBot[]
  connected: boolean
  /** False when SECRETS_KEY is absent - nothing can be stored safely. */
  secrets_ready: boolean
  source: "console" | "environment" | "unset"
  bot_username: string
  chat_id: string
  token_masked: string
  connected_by: string
  connected_at: string
}

function Section({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <Card className="profile-section">
      <CardHeader className="profile-section-header">
        <div className="flex items-start gap-3">
          <span className="profile-section-icon">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0">
            <CardTitle>{title}</CardTitle>
            <CardDescription className="mt-1 leading-5">
              {description}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="profile-section-content">{children}</CardContent>
    </Card>
  )
}

/** Name and picture. */
function IdentitySection() {
  const { profile, save } = useProfile()
  const [name, setName] = React.useState(profile.name)
  const [preview, setPreview] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [uploading, setUploading] = React.useState(false)
  const fileInput = React.useRef<HTMLInputElement>(null)

  // Track whether this field has been TYPED IN rather than seeding it once and
  // freezing it.
  //
  // The profile is now a shared query that revalidates on focus, so its value
  // legitimately changes under this component - a rename on a phone lands here
  // while the page is open. Seeding once meant the sidebar showed the new name
  // and this box still showed the old one. Mirroring it unconditionally would
  // be worse: it would overwrite whatever is half-typed the moment a refetch
  // returned. So the server wins until the user touches the field, and the
  // user wins from then until the save lands.
  const [dirty, setDirty] = React.useState(false)
  React.useEffect(() => {
    if (!dirty) setName(profile.name)
  }, [profile.name, dirty])

  // An object URL is a live handle into browser memory; letting it leak means
  // the decoded image is never freed.
  React.useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview)
    }
  }, [preview])

  async function onPick(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Clear immediately, so choosing the SAME file again still fires change.
    event.target.value = ""
    if (!file) return

    setUploading(true)
    try {
      const image = await compressAvatar(file)
      setPreview((old) => {
        if (old) URL.revokeObjectURL(old)
        return image.objectUrl
      })
      const { url } = await postBytes<{ url: string }>(
        "/api/profile/avatar",
        image.blob,
      )
      await save({ avatarUrl: url })
      toast.success("Picture updated", {
        description:
          `Compressed to ${Math.max(1, Math.round(image.blob.size / 1024))} KB ` +
          `from ${Math.max(1, Math.round(file.size / 1024))} KB.`,
      })
    } catch (error) {
      setPreview(null)
      toast.error(
        error instanceof Error ? error.message : "Could not use that image.",
      )
    } finally {
      setUploading(false)
    }
  }

  async function onRemove() {
    setUploading(true)
    try {
      await del("/api/profile/avatar")
      await save({ avatarUrl: null })
      setPreview((old) => {
        if (old) URL.revokeObjectURL(old)
        return null
      })
      toast.success("Picture removed")
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not remove that.",
      )
    } finally {
      setUploading(false)
    }
  }

  async function onSaveName() {
    setBusy(true)
    try {
      await save({ name })
      // The field now matches the server again, so let the live value drive
      // it once more.
      setDirty(false)
      toast.success("Profile saved")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save that.")
    } finally {
      setBusy(false)
    }
  }

  const shown = preview ?? profile.avatarUrl

  return (
    <Section icon={CircleUserRound} title="Personal details" description="A familiar face and name for your workspace.">
      <div className="profile-identity">
        <div className="profile-photo-row">
          <div className="profile-photo-frame">
            <UserAvatar key={shown ?? "none"} src={shown} name={name || profile.displayName} seed={profile.email} className="size-20 shrink-0 text-2xl" />
          </div>
          <div className="profile-photo-copy">
            <h3>Profile photo</h3>
            <p>JPG, PNG or WebP. We’ll resize it for you.</p>
            <div className="profile-photo-actions">
              <input ref={fileInput} type="file" accept="image/*" aria-label="Upload profile photo" className="hidden" onChange={event => void onPick(event)} />
              <Button size="sm" variant="default" disabled={uploading} onClick={() => fileInput.current?.click()}>
                <Upload />{uploading ? "Updating…" : shown ? "Change photo" : "Upload photo"}
              </Button>
              {shown && <Button size="sm" variant="ghost" disabled={uploading} onClick={() => void onRemove()}><Trash2 /> Remove</Button>}
            </div>
          </div>
        </div>
        <div className="profile-fields">
          <div className="profile-field">
            <label htmlFor="display-name">Display name</label>
            <Input id="display-name" value={name} placeholder={profile.email.split("@")[0]} onChange={event => { setDirty(true); setName(event.target.value) }} />
            <p>Shown beside your review decisions.</p>
          </div>
          <div className="profile-field">
            <label htmlFor="account-email">Email address</label>
            <Input id="account-email" value={profile.email} disabled readOnly />
            <p>Managed by your sign-in provider.</p>
          </div>
        </div>
        <div className="profile-save-row">
          <span>{dirty ? "You have unsaved changes" : "Your details are shared across the workspace."}</span>
          <Button variant="brand" disabled={busy || !dirty} onClick={() => void onSaveName()}><Check />{busy ? "Saving…" : "Save changes"}</Button>
        </div>
      </div>
    </Section>
  )
}

/** Light / dark. */
function AppearanceSection() {
  const { dark, setDark } = useTheme()
  const options = [
    { value: false, label: "Light", icon: Sun },
    { value: true, label: "Dark", icon: Moon },
  ]
  return (
    <Section icon={Palette} title="Appearance" description="Choose the canvas you feel at home in. Saved on this device.">
      <div className="profile-theme-options">
        {options.map(({ value, label, icon: Icon }) => (
          <button key={label} type="button" onClick={() => setDark(value)} aria-pressed={dark === value} className="profile-theme-option" data-appearance={value ? "dark" : "light"}>
            <span className="profile-theme-preview" aria-hidden="true">
              <span className="profile-preview-sidebar"><img src="/illustrations/carousel-sculpture-160.webp" alt="" /><i /><i /><i /></span>
              <span className="profile-preview-main"><span className="profile-preview-title" /><span className="profile-preview-line" /><span className="profile-preview-composer"><i /></span><span className="profile-preview-cards"><i /><i /><i /></span></span>
            </span>
            <span className="profile-theme-label"><Icon /><strong>{label}</strong><span className="profile-theme-check" aria-hidden="true">{dark === value && <Check />}</span></span>
            <span className="profile-theme-caption">{value ? "Pitch black. A quieter workspace." : "Warm ivory. Room for fresh ideas."}</span>
          </button>
        ))}
      </div>
    </Section>
  )
}

/**
 * Connect the review bot.
 *
 * The chat id is DISCOVERED rather than asked for - typing a numeric chat id
 * is the step everyone gets wrong. Telegram will only name a chat once a human
 * has messaged the bot, so "no chat yet" renders as a step to finish, not as
 * an error.
 */
function TelegramSection() {
  const queryClient = useQueryClient()
  const [token, setToken] = React.useState("")
  const [needsStart, setNeedsStart] = React.useState("")

  const status = useQuery({
    queryKey: ["telegram"],
    queryFn: () => get<TelegramStatus>("/api/settings/telegram"),
    // Opening this screen always re-asks. Connecting the bot is the kind of
    // thing done once, from whichever device is to hand - so the answer this
    // browser happens to be holding is exactly the one likely to be wrong,
    // and it is one small request to be certain instead.
    refetchOnMount: "always",
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["telegram"] })
    // The review screen warns when publishing is unconfigured; it reads /meta.
    void queryClient.invalidateQueries({ queryKey: ["meta"] })
  }

  const connect = useMutation({
    mutationFn: () => post<TelegramStatus>("/api/settings/telegram", { token }),
    onSuccess: () => {
      setToken("")
      setNeedsStart("")
      toast.success("Telegram connected", {
        description: "A hello message is waiting in your chat.",
      })
      refresh()
    },
    onError: (error) => {
      const code = error instanceof ApiError ? error.code : undefined
      if (code === "no_chat") {
        const detail = error instanceof ApiError ? error.detail : undefined
        const username =
          (detail as { bot_username?: string } | undefined)?.bot_username ?? ""
        setNeedsStart(username || "your_bot")
        return
      }
      toast.error(error instanceof Error ? error.message : "Could not connect.")
    },
  })

  const disconnect = useMutation({
    mutationFn: (botId: string) => del<TelegramStatus>(`/api/settings/telegram/${encodeURIComponent(botId)}`),
    onSuccess: () => {
      toast.success("Bot disconnected")
      refresh()
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not disconnect."),
  })

  const data = status.data
  // First visit to this screen has nothing cached, and the section rendered
  // its "not connected" instructions while the answer was still in flight -
  // telling a connected user to go and make a bot. Hold the space instead.
  const unknown = status.isLoading && !data
  const connected = !!data?.connected
  const bots = data?.bots ?? []
  // Say so BEFORE someone types a bearer token into a form that will refuse
  // it - the server will not store a credential it cannot encrypt.
  const secretsMissing = data ? !data.secrets_ready : false

  return (
    <Section
      icon={Radio}
      title="Telegram"
      description="Receive carousels, review requests, and publishing updates in your Telegram chats."
    >
      {unknown && (
        <div className="space-y-3">
          <Skeleton className="h-6 w-32 rounded-full" />
          <Skeleton className="h-4 w-full max-w-md" />
          <Skeleton className="h-4 w-full max-w-sm" />
          <Skeleton className="h-9 w-full" />
        </div>
      )}

      {!unknown && bots.length > 0 && (
        <ul className="mb-4 space-y-2">
          {bots.map((bot) => (
            <li key={bot.bot_id} className="profile-connected-account">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">@{bot.bot_username || bot.bot_id}</p>
                <p className="text-xs text-[var(--muted-foreground)]">Chat {bot.chat_id} · {bot.token_masked}</p>
              </div>
              <Chip tone={bot.connected ? "done" : "failed"}>{bot.connected ? "Connected" : "Reconnect needed"}</Chip>
              <Button size="sm" variant="ghost" disabled={disconnect.isPending} onClick={() => disconnect.mutate(bot.bot_id)}>
                <Unplug /> Disconnect
              </Button>
            </li>
          ))}
        </ul>
      )}

      {secretsMissing && (
        <p
          className="mb-4 rounded-[var(--radius-md)] px-3 py-2 text-sm"
          style={{
            background: "var(--phase-failed-soft)",
            color: "var(--phase-failed-fg)",
          }}
        >
          SECRETS_KEY is not set on the server, so a bot token cannot be stored
          encrypted - and it will not be stored any other way. Generate a key
          and put it in .env, then reload.
        </p>
      )}

      {unknown ? null : (
        <div className="space-y-3">
          <ol className="profile-connection-steps">
            <li>
              1. Message{" "}
              <a
                className="text-[var(--link)] hover:underline"
                href="https://t.me/BotFather"
                target="_blank"
                rel="noreferrer"
              >
                @BotFather <ExternalLink className="inline size-3" />
              </a>{" "}
              and send <code className="font-mono">/newbot</code>.
            </li>
            <li>2. Paste the token it gives you below.</li>
            <li>3. Send your new bot a message, so it knows where to reply.</li>
          </ol>

          <label className="profile-input-label" htmlFor="telegram-bot-token">Bot token</label>
          <div className="profile-token-actions">
            <Input
              type="password"
              id="telegram-bot-token"
              aria-label="Telegram bot token"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="123456789:AA..."
              autoComplete="off"
              spellCheck={false}
              className="min-w-0 flex-1 font-mono"
            />
            <Button
              variant="brand"
              disabled={!token.trim() || connect.isPending || secretsMissing}
              onClick={() => connect.mutate()}
            >
              <Send /> {connect.isPending ? "Connecting..." : connected ? "Add another bot" : "Connect bot"}
            </Button>
          </div>

          {needsStart && (
            <p
              className="rounded-[var(--radius-md)] px-3 py-2 text-sm"
              style={{
                background: "var(--phase-review-soft)",
                color: "var(--phase-review-fg)",
              }}
            >
              The bot is real, but nobody has messaged it - Telegram only names
              a chat once a human has written to it. Open{" "}
              <a
                className="font-medium underline"
                href={"https://t.me/" + needsStart}
                target="_blank"
                rel="noreferrer"
              >
                t.me/{needsStart}
              </a>
              , send <code className="font-mono">/start</code>, then press
              Connect again.
            </p>
          )}
        </div>
      )}

    </Section>
  )
}

/** What each callback error code means, in words a person can act on. */
const INSTAGRAM_ERRORS: Record<string, string> = {
  bad_state:
    "That connection request could not be verified. Start it again from this page.",
  state_expired: "That connection request took too long. Press Connect again.",
  no_code: "Instagram did not send an authorisation code back.",
  no_identity:
    "Instagram would not identify that account. It must be a Professional (Business or Creator) account.",
  secrets_unconfigured:
    "SECRETS_KEY is not set on the server, so the token could not be stored encrypted. Nothing was saved.",
  not_configured:
    "This console has no Meta app credentials (IG_APP_ID / IG_APP_SECRET).",
  no_public_url: "PUBLIC_BASE_URL is not set on the server.",
  bad_token: "Paste an access token first.",
  id_mismatch:
    "That token belongs to a different account than the id you typed. Nothing was saved.",
}

/**
 * Connecting Instagram accounts.
 *
 * Nobody types an Instagram password here. "Connect" is a plain link to our
 * own authorize route, which redirects to Instagram's login page; the browser
 * comes back to /profile carrying ?instagram=connected or ?instagram_error=...
 * A full navigation rather than a fetch, because that is what OAuth is.
 *
 * The account is what a run is generated FOR, not merely published to - its
 * handle and picture are stamped onto every slide - which is why disconnecting
 * one is described as affecting future runs rather than as housekeeping.
 */
function InstagramSection() {
  const queryClient = useQueryClient()

  const status = useQuery({
    queryKey: ["instagram"],
    queryFn: () => get<InstagramStatus>("/api/settings/instagram"),
    // Same reasoning as Telegram: connecting is done once, from whichever
    // device is to hand, so a cached answer here is the one likely to be stale.
    refetchOnMount: "always",
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["instagram"] })
    // The newsroom and review screens read /meta for publish_configured and
    // for the account picker.
    void queryClient.invalidateQueries({ queryKey: ["meta"] })
  }

  // Read the outcome the callback redirected back with, then scrub it from the
  // URL so a reload does not re-announce a connection that happened minutes ago.
  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const connected = params.get("instagram")
    const failed = params.get("instagram_error")
    if (!connected && !failed) return

    if (connected) {
      const account = params.get("account")
      toast.success(
        account ? `Connected @${account}` : "Instagram connected",
      )
      refresh()
    } else if (failed === "access_denied") {
      toast("Connection cancelled", {
        description: "Nothing was changed.",
      })
    } else {
      toast.error("Could not connect that account", {
        description: params.get("detail") || INSTAGRAM_ERRORS[failed!] || failed!,
      })
    }

    params.delete("instagram")
    params.delete("instagram_error")
    params.delete("account")
    params.delete("detail")
    const query = params.toString()
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (query ? `?${query}` : ""),
    )
    // Once, on mount. The URL is scrubbed immediately, so there is nothing to
    // react to afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setDefault = useMutation({
    mutationFn: (accountId: string) =>
      post<InstagramStatus>("/api/settings/instagram/default", {
        account_id: accountId,
      }),
    onSuccess: () => {
      toast.success("Default account updated")
      refresh()
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not update."),
  })

  const disconnect = useMutation({
    mutationFn: (accountId: string) =>
      del<InstagramStatus>(`/api/settings/instagram/${accountId}`),
    onSuccess: () => {
      toast.success("Account disconnected")
      refresh()
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not disconnect."),
  })

  const [pastedToken, setPastedToken] = React.useState("")
  const [pastedId, setPastedId] = React.useState("")

  const paste = useMutation({
    mutationFn: () =>
      post<InstagramConnectResponse>("/api/settings/instagram/token", {
        token: pastedToken,
        ig_user_id: pastedId,
      }),
    onSuccess: (result) => {
      setPastedToken("")
      setPastedId("")
      const days = result.account.expires_in_days
      toast.success(`Connected ${result.account.handle}`, {
        description:
          result.expiry === "confirmed"
            ? `Instagram refreshed the token; it is good for ${days} days.`
            : `Instagram would not refresh this token, so its expiry is assumed to be ${days} days. Reconnect if publishing starts failing.`,
      })
      refresh()
    },
    onError: (error) => {
      const code = error instanceof ApiError ? error.code : undefined
      toast.error("Could not connect that token", {
        description:
          (code && INSTAGRAM_ERRORS[code]) ||
          (error instanceof Error ? error.message : "Something went wrong."),
      })
    },
  })

  const data = status.data
  const unknown = status.isLoading && !data
  const accounts = data?.accounts ?? []
  const secretsMissing = data ? !data.secrets_ready : false

  return (
    <Section
      icon={Instagram}
      title="Instagram"
      description="Connect your Instagram accounts to publish approved carousels."
    >
      {unknown && (
        <div className="space-y-3">
          <Skeleton className="h-6 w-32 rounded-full" />
          <Skeleton className="h-4 w-full max-w-md" />
          <Skeleton className="h-9 w-full" />
        </div>
      )}

      {secretsMissing && (
        <p
          className="mb-4 rounded-[var(--radius-md)] px-3 py-2 text-sm"
          style={{
            background: "var(--phase-failed-soft)",
            color: "var(--phase-failed-fg)",
          }}
        >
          SECRETS_KEY is not set on the server, so an access token cannot be
          stored encrypted - and it will not be stored any other way. Generate
          a key and put it in .env, then reload.
        </p>
      )}

      {!unknown && accounts.length > 0 && (
        <ul className="mb-4 space-y-2">
          {accounts.map((account) => (
            <li
              key={account.id}
              className="profile-connected-account"
            >
              <span className="profile-connection-avatar" aria-hidden="true"><Instagram />
              <img
                src={`/api/settings/instagram/${account.id}/avatar`}
                alt=""
                width={32}
                height={32}
                className="size-8 shrink-0 rounded-full object-cover"
                // No stored picture is an ordinary state, not a broken image.
                onError={(event) => {
                  event.currentTarget.style.display = "none"
                }}
              /></span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-sm font-medium">
                    {account.handle}
                  </span>
                  {account.is_default && <Chip tone="done">Default</Chip>}
                  {account.needs_reconnect ? (
                    <MutedChip>Needs reconnecting</MutedChip>
                  ) : (
                    account.expires_in_days !== null &&
                    account.expires_in_days <= 14 && (
                      <MutedChip>
                        Expires in {account.expires_in_days}d
                      </MutedChip>
                    )
                  )}
                </div>
                {account.name && (
                  <p className="truncate text-xs text-[var(--muted-foreground)]">
                    {account.name}
                  </p>
                )}
              </div>
              {!account.is_default && !account.needs_reconnect && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={setDefault.isPending}
                  onClick={() => setDefault.mutate(account.id)}
                >
                  <Check /> Make default
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                disabled={disconnect.isPending}
                onClick={() => disconnect.mutate(account.id)}
              >
                <Unplug /> Disconnect
              </Button>
            </li>
          ))}
        </ul>
      )}

      {!unknown && (
        <div className="space-y-4">
          <p className="text-sm text-[var(--muted-foreground)]">
            Add a separate token for each Professional (Business or Creator) account.
            You can connect multiple accounts and choose one before starting a carousel.
            Publishing to that account always requires your approval.
          </p>
          <div className="profile-token-form">
            <details className="profile-setup-help">
              <summary>Where do I find my access token?</summary>
            <p className="text-sm text-[var(--muted-foreground)]">
              In the{" "}
              <a
                className="text-[var(--link)] hover:underline"
                href="https://developers.facebook.com/apps/"
                target="_blank"
                rel="noreferrer"
              >
                Meta app dashboard <ExternalLink className="inline size-3" />
              </a>
              , open Instagram → API setup with Instagram login, and press
              Generate token on the account you want. Paste it here. The
              handle, name and picture are read from the token, so there is
              nothing else to fill in.
            </p>
            </details>
            <label className="profile-input-label" htmlFor="instagram-access-token">Access token</label>

            <Input
              type="password"
              value={pastedToken}
              onChange={(event) => setPastedToken(event.target.value)}
              placeholder="IGAAWwgws..."
              autoComplete="off"
              spellCheck={false}
              className="w-full font-mono"
              aria-label="Instagram access token"
              id="instagram-access-token"
            />

            <div className="profile-token-actions">
              <Input
                value={pastedId}
                onChange={(event) => setPastedId(event.target.value)}
                placeholder="Optional user ID"
                autoComplete="off"
                spellCheck={false}
                inputMode="numeric"
                className="min-w-0 flex-1 font-mono"
                aria-label="Instagram user id"
              />
              <Button
                variant="brand"
                disabled={
                  !pastedToken.trim() || paste.isPending || secretsMissing
                }
                onClick={() => paste.mutate()}
              >
                <KeyRound />{" "}
                {paste.isPending ? "Connecting..." : "Connect account"}
              </Button>
            </div>

            <p className="text-xs text-[var(--muted-foreground)]">
              The id is only needed for a token generated through Facebook
              login, which cannot say which account it is for. With an
              Instagram login token it acts as a check: if the token turns
              out to belong to a different account, nothing is saved.
            </p>
          </div>
        </div>
      )}
    </Section>
  )
}

export function ProfileRoute() {
  const { profile } = useProfile()
  type SettingsView = "account" | "appearance" | "connections" | "ai" | "tracing"
  const [view, setView] = React.useState<SettingsView>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.has("instagram") || params.has("instagram_error")
      ? "connections"
      : params.get("view") === "tracing" ? "tracing" : params.get("view") === "ai" ? "ai" : "account"
  })

  const views = [
    {
      value: "account" as const,
      label: "Account",
      icon: <CircleUserRound />,
    },
    {
      value: "appearance" as const,
      label: "Appearance",
      icon: <Palette />,
    },
    {
      value: "connections" as const,
      label: "Connections",
      icon: <Cable />,
    },
    { value: "ai" as const, label: "AI & models", icon: <KeyRound /> },
    { value: "tracing" as const, label: "Tracing", icon: <Activity /> },
  ]

  return (
    <div className="profile-page">
      <header className="profile-page-heading">
        <div><p className="profile-eyebrow">Your workspace</p><h1>Profile &amp; settings</h1><p>A little more you. Everywhere you create.</p></div>
        <StudioArtwork name="design-stylus" sizes="96px" />
      </header>
      <div className="profile-overview">
        <UserAvatar key={profile.avatarUrl ?? "none"} src={profile.avatarUrl} name={profile.displayName} seed={profile.email} className="profile-overview-avatar" />
        <div><span className="profile-overview-label">Your profile</span><h2>{profile.displayName}</h2><p>{profile.email}</p></div>
        <span className="profile-overview-note"><CircleUserRound /> Your studio account</span>
      </div>
      <Tabs items={views} value={view} onChange={setView} label="Settings views" className="profile-tabs" />
      <TabPanel value="ai" selected={view === "ai"}><AISettingsSection /></TabPanel>
      <TabPanel value="tracing" selected={view === "tracing"}><TracingSettingsSection /></TabPanel>
      <TabPanel value="account" selected={view === "account"}><IdentitySection /></TabPanel>
      <TabPanel value="appearance" selected={view === "appearance"}><AppearanceSection /></TabPanel>
      <TabPanel value="connections" selected={view === "connections"} className="profile-connections">
        <InstagramSection />
        <TelegramSection />
      </TabPanel>
    </div>
  )
}
