import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Activity, ShieldCheck, Unplug } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { del, get, post } from "@/lib/api"

type TracingStatus = {
  enabled: boolean; base_url: string; key_configured: boolean; key_error: boolean; secrets_ready: boolean; can_edit: boolean
}
const queryKey = ["settings", "tracing"]

export function TracingSettingsSection() {
  const client = useQueryClient()
  const query = useQuery({ queryKey, queryFn: () => get<TracingStatus>("/api/settings/tracing") })
  const [draft, setDraft] = React.useState<{ enabled: boolean; base_url: string } | null>(null)
  const [publicKey, setPublicKey] = React.useState("")
  const [secretKey, setSecretKey] = React.useState("")
  const [working, setWorking] = React.useState(false)
  const [error, setError] = React.useState("")
  const status = query.data
  const fields = draft ?? status ?? { enabled: false, base_url: "https://cloud.langfuse.com" }
  const changed = Boolean(publicKey.trim() || secretKey.trim()) || Boolean(draft && (draft.enabled !== status?.enabled || draft.base_url !== status?.base_url))

  async function update(disconnect = false) {
    setWorking(true); setError("")
    try {
      const result = disconnect ? await del<TracingStatus>("/api/settings/tracing") : await post<TracingStatus>("/api/settings/tracing", {
        enabled: fields.enabled, base_url: fields.base_url.trim(), public_key: publicKey.trim(), secret_key: secretKey.trim(),
      })
      client.setQueryData(queryKey, result)
      setDraft(null); setPublicKey(""); setSecretKey("")
      toast.success(disconnect ? "Langfuse disconnected" : "Tracing settings saved")
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save tracing settings. Try again.") }
    finally { setWorking(false) }
  }

  return <Card className="profile-section">
    <CardHeader className="profile-section-header"><div className="flex items-start gap-3">
      <span className="profile-section-icon"><Activity className="size-4" /></span>
      <div className="min-w-0"><CardTitle>Tracing &amp; tokens</CardTitle><CardDescription className="mt-1 leading-5">Your trace page works automatically. Connect Langfuse for additional workspace analytics.</CardDescription></div>
    </div></CardHeader>
    <CardContent className="profile-section-content">
      {query.isPending ? <Skeleton className="h-72 w-full" /> : !status ? <div className="profile-ai-error" role="alert"><p>Tracing settings could not be loaded.</p><Button onClick={() => query.refetch()}>Try again</Button></div> :
        <form className="profile-ai-form" onSubmit={event => { event.preventDefault(); void update() }}>
          <div className="profile-ai-key-status"><ShieldCheck aria-hidden="true" /><div><strong>Token tracking is always on</strong><p>Counts come directly from agent responses and image generation. Langfuse is optional and does not control the trace page.</p></div></div>
          <div><h3 className="text-sm font-semibold">Langfuse</h3><p className="profile-ai-note" role="status">{status.key_error ? "Saved keys need attention. Enter both keys again." : status.enabled && status.key_configured ? "Connected. New and resumed runs export tracing data." : status.key_configured ? "Keys saved. Export is turned off." : "Not connected. Your local traces and tokens are still recorded."}</p></div>
          {!status.can_edit && <p className="profile-ai-note">An administrator can change these workspace settings.</p>}
          <fieldset disabled={!status.can_edit || working} className="profile-ai-fields">
            <label className="profile-tracing-toggle"><input type="checkbox" checked={fields.enabled} onChange={event => setDraft({ ...fields, enabled: event.target.checked })} /><span><strong>Export traces to Langfuse</strong><small>Agent timings, tool calls and token usage. Prompt and response contents are hidden.</small></span></label>
            <div className="profile-field"><label htmlFor="langfuse-url">Langfuse server URL</label><Input id="langfuse-url" type="url" required value={fields.base_url} onChange={event => setDraft({ ...fields, base_url: event.target.value })} placeholder="https://cloud.langfuse.com" />
              <p>Use the URL for your project's region or your HTTPS self-hosted server.</p></div>
            <div className="profile-fields profile-ai-models">
              <div className="profile-field"><label htmlFor="langfuse-public">Public key</label><Input id="langfuse-public" type="password" autoComplete="new-password" autoCapitalize="none" spellCheck={false} maxLength={210} disabled={!status.secrets_ready}
                value={publicKey} onChange={event => setPublicKey(event.target.value)} placeholder={status.key_configured ? "Leave blank to keep saved key" : "pk-lf-..."} /></div>
              <div className="profile-field"><label htmlFor="langfuse-secret">Secret key</label><Input id="langfuse-secret" type="password" autoComplete="new-password" autoCapitalize="none" spellCheck={false} maxLength={210} disabled={!status.secrets_ready}
                value={secretKey} onChange={event => setSecretKey(event.target.value)} placeholder={status.key_configured ? "Leave blank to keep saved key" : "sk-lf-..."} /></div>
            </div>
            <p className="profile-ai-note">{status.secrets_ready ? "Both keys are stored encrypted. Leave them blank to keep your current connection, or enter both to change projects." : "The server needs credential encryption configured before you can save keys."}</p>
            {error && <p className="profile-ai-error" role="alert">{error}</p>}
            <div className="profile-save-row"><span>Changes apply to new and resumed runs.</span>
              {status.key_configured || status.key_error ? <Button type="button" variant="ghost" size="sm" onClick={() => void update(true)}><Unplug />Disconnect</Button> : null}
              <Button type="submit" variant="brand" disabled={!changed}>{working ? "Saving…" : "Save tracing settings"}</Button>
            </div>
          </fieldset>
        </form>}
    </CardContent>
  </Card>
}
