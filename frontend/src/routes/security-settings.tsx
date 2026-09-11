import * as React from "react"
import { Link } from "react-router"
import { Fingerprint, ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/hooks/use-auth"
import { activeSession, authCapabilities, authError } from "@/lib/auth-flow"

type SavedPasskey = { id: string; friendly_name?: string | null }

export function SecuritySettingsSection() {
  const { identity, completeSignIn } = useAuth()
  const [factor, setFactor] = React.useState("")
  const [unfinished, setUnfinished] = React.useState(false)
  const [pending, setPending] = React.useState<{ id: string; qr: string; secret: string } | null>(null)
  const [removingFactor, setRemovingFactor] = React.useState(false)
  const [removeKey, setRemoveKey] = React.useState("")
  const [code, setCode] = React.useState("")
  const [passkeys, setPasskeys] = React.useState(false)
  const [savedKeys, setSavedKeys] = React.useState<SavedPasskey[]>([])
  const [busy, setBusy] = React.useState(false)
  const [ready, setReady] = React.useState(false)
  const [error, setError] = React.useState("")
  const [notice, setNotice] = React.useState("")
  const guard = React.useRef(false)

  async function ownClient() {
    const { client, session } = await activeSession()
    if (session.user.id !== identity?.id) throw new Error("Sign in to this account again before changing security settings.")
    return client
  }
  async function load() {
    const client = await ownClient()
    const { data, error: failure } = await client.auth.mfa.listFactors()
    if (failure) throw failure
    setFactor(data.totp.find(item => item.status === "verified")?.id ?? "")
    setUnfinished(data.all.some(item => item.status === "unverified" && item.factor_type === "totp"))
    setReady(true)
  }
  async function loadKeys() {
    const client = await ownClient()
    const { data, error: failure } = await client.auth.passkey.list()
    if (failure) throw failure
    setSavedKeys(data)
  }
  React.useEffect(() => {
    void load().catch(cause => setError(authError(cause)))
    void authCapabilities().then(async value => {
      setPasskeys(value.passkeys)
      if (value.passkeys) await loadKeys()
    }).catch(cause => setError(authError(cause)))
  }, [])

  async function action(fn: () => Promise<void>) {
    if (guard.current) return
    guard.current = true
    setBusy(true); setError(""); setNotice("")
    try { await fn() } catch (cause) { setError(authError(cause)) }
    finally { guard.current = false; setBusy(false) }
  }
  async function enroll() {
    const client = await ownClient()
    const { data, error: failure } = await client.auth.mfa.enroll({ factorType: "totp", friendlyName: "Carousel authenticator " + Date.now() })
    if (failure) throw failure
    setPending({ id: data.id, qr: data.totp.qr_code, secret: data.totp.secret })
  }
  async function verify() {
    if (!pending) return
    const client = await ownClient()
    const { data, error: failure } = await client.auth.mfa.challengeAndVerify({ factorId: pending.id, code })
    if (failure) throw failure
    await completeSignIn(data.access_token)
    setPending(null); setCode("")
    await load()
    setNotice("Two-factor authentication is on.")
  }
  async function cancel() {
    if (!pending) return
    const client = await ownClient()
    const { error: failure } = await client.auth.mfa.unenroll({ factorId: pending.id })
    if (failure) throw failure
    setPending(null); setCode("")
  }
  async function disableFactor() {
    const client = await ownClient()
    const verified = await client.auth.mfa.challengeAndVerify({ factorId: factor, code })
    if (verified.error) throw verified.error
    const removed = await client.auth.mfa.unenroll({ factorId: factor })
    if (removed.error) throw removed.error
    const refreshed = await client.auth.refreshSession()
    if (refreshed.error || !refreshed.data.session) throw refreshed.error ?? new Error("Please sign in again to refresh your session.")
    await completeSignIn(refreshed.data.session.access_token)
    setRemovingFactor(false); setCode("")
    await load()
    setNotice("Authenticator removed.")
  }
  const codeInput = <label className="auth-field">Authenticator code<Input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ""))} required /></label>

  return <Card className="profile-section">
    <CardHeader className="profile-section-header"><div className="flex items-start gap-3"><span className="profile-section-icon"><ShieldCheck className="size-4" /></span><div><CardTitle>Account security</CardTitle><CardDescription className="mt-1">Choose how you protect your private workspace.</CardDescription></div></div></CardHeader>
    <CardContent className="profile-section-content auth-security-actions">
      {error && <p role="alert" className="auth-alert">{error}</p>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      <div><h3 className="text-sm font-medium">Authenticator app</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">{factor ? "Enabled. Sign-in requires your authenticator code." : "Add a second step after your password, email code or social sign-in."}</p></div>
      {unfinished && !pending && <Button variant="ghost" disabled={busy} onClick={() => void action(async () => {
        const client = await ownClient()
        const result = await client.auth.mfa.listFactors()
        if (result.error) throw result.error
        for (const item of result.data.all.filter(item => item.status === "unverified" && item.factor_type === "totp")) {
          const removed = await client.auth.mfa.unenroll({ factorId: item.id })
          if (removed.error) throw removed.error
        }
        await load(); setNotice("Unfinished authenticator setup cleared. You can start again.")
      })}>Discard unfinished setup</Button>}
      {pending ? <form className="auth-form" onSubmit={event => { event.preventDefault(); void action(verify) }}>
        <img className="auth-security-qr" src={pending.qr.startsWith("data:image/") ? pending.qr : "data:image/svg+xml;charset=utf-8," + encodeURIComponent(pending.qr)} alt="Scan this QR code with your authenticator app" />
        <details><summary className="text-xs">Can’t scan the code?</summary><p className="auth-security-secret">{pending.secret}</p></details>
        <p className="text-xs text-[var(--muted-foreground)]">Keep a secure backup in your authenticator app. Email password resets do not remove this second step.</p>
        {codeInput}
        <div className="flex flex-wrap gap-2"><Button type="submit" variant="brand" disabled={busy}>Verify and enable</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => void action(cancel)}>Cancel setup</Button></div>
      </form> : removingFactor ? <form className="auth-form" onSubmit={event => { event.preventDefault(); void action(disableFactor) }}>
        <p className="text-xs text-[var(--muted-foreground)]">Enter your current code to remove this authenticator. Your account will no longer require its second step.</p>{codeInput}
        <div className="flex flex-wrap gap-2"><Button type="submit" variant="secondary" disabled={busy}>Verify and remove</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => { setRemovingFactor(false); setCode("") }}>Keep authenticator</Button></div>
      </form> : !factor ? <Button variant="secondary" disabled={busy || !ready} onClick={() => void action(enroll)}>Set up authenticator</Button> : <Button variant="ghost" disabled={busy} onClick={() => setRemovingFactor(true)}>Remove authenticator</Button>}
      {passkeys && <>
        <div className="border-t border-[var(--border)] pt-4"><h3 className="text-sm font-medium">Passkeys</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">Use your fingerprint, face or device lock to sign in.</p></div>
        {savedKeys.map((key, index) => <div key={key.id} className="rounded-xl border border-[var(--border)] p-3 text-xs">
          <div className="flex items-center justify-between gap-3"><span className="min-w-0 break-words">{key.friendly_name || `Passkey ${index + 1}`}</span><Button size="sm" variant="ghost" disabled={busy} onClick={() => setRemoveKey(key.id)}>Remove</Button></div>
          {removeKey === key.id && <div className="mt-2"><p>This passkey will stop working. You can still sign in with an email code or another saved method.</p><div className="mt-2 flex flex-wrap gap-2"><Button size="sm" variant="secondary" disabled={busy} onClick={() => void action(async () => { const client = await ownClient(); const result = await client.auth.passkey.delete({ passkeyId: key.id }); if (result.error) throw result.error; setRemoveKey(""); await loadKeys(); setNotice("Passkey removed.") })}>Remove passkey</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => setRemoveKey("")}>Keep passkey</Button></div></div>}
        </div>)}
        {typeof window.PublicKeyCredential !== "undefined" && <Button variant="secondary" disabled={busy || !ready} onClick={() => void action(async () => { const client = await ownClient(); const { error: failure } = await client.auth.registerPasskey(); if (failure) throw failure; await loadKeys(); setNotice("Passkey added. You can use it next time you sign in.") })}><Fingerprint size={16} /> Add a passkey</Button>}
      </>}
      <Link to="/forgot-password" className="text-xs underline underline-offset-4">Reset your password</Link>
    </CardContent>
  </Card>
}
