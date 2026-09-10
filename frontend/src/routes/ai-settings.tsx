import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Cpu, KeyRound, RefreshCw, ShieldCheck } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { get, post } from "@/lib/api"

const suggested = {
  planner_model: "openai/gpt-5.6-sol",
  utility_model: "openai/gpt-5.4-mini",
  phrasing_model: "openai/gpt-5.6-sol",
  image_model: "gpt-image-2",
}
type Models = typeof suggested
type ModelCatalog = { text_models: string[]; image_models: string[] }
type AIStatus = Models & {
  key_configured: boolean
  key_source: "saved" | "unset"
  key_error: boolean
  secrets_ready: boolean
  can_edit: boolean
}
const roles: { name: keyof Models; label: string; description: string }[] = [
  { name: "planner_model", label: "Planner", description: "Research, story structure and slide planning." },
  { name: "utility_model", label: "Utility", description: "Web search, visuals, layout and workflow tasks." },
  { name: "phrasing_model", label: "Phrasing", description: "Headlines, slide copy and final wording." },
  { name: "image_model", label: "Image", description: "Image generation and image edits." },
]
const queryKey = ["settings", "ai"]

export function AISettingsSection() {
  const client = useQueryClient()
  const query = useQuery({ queryKey, queryFn: () => get<AIStatus>("/api/settings/ai") })
  const [draft, setDraft] = React.useState<Models | null>(null)
  const [key, setKey] = React.useState("")
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState("")
  const [catalog, setCatalog] = React.useState<ModelCatalog | null>(null)
  const [loadingModels, setLoadingModels] = React.useState(false)
  const [modelError, setModelError] = React.useState("")
  const modelRequest = React.useRef(0)
  const status = query.data
  const models = draft ?? status ?? suggested
  const changed = Boolean(key.trim()) || (draft !== null && roles.some(({ name }) => draft[name] !== status?.[name]))
  const allModelsAvailable = Boolean(catalog && roles.every(({ name }) =>
    catalog[name === "image_model" ? "image_models" : "text_models"].includes(models[name]),
  ))

  const loadModels = React.useCallback(async () => {
    const request = ++modelRequest.current
    setLoadingModels(true)
    setModelError("")
    setCatalog(null)
    try {
      const result = key.trim()
        ? await post<ModelCatalog>("/api/settings/ai/models", { api_key: key.trim() })
        : await get<ModelCatalog>("/api/settings/ai/models")
      if (request === modelRequest.current) setCatalog(result)
    } catch (err) {
      if (request === modelRequest.current) setModelError(err instanceof Error ? err.message : "Could not load models. Try again.")
    } finally {
      if (request === modelRequest.current) setLoadingModels(false)
    }
  }, [key])

  React.useEffect(() => {
    if (status?.key_configured && !key.trim()) void loadModels()
    return () => { modelRequest.current += 1 }
  }, [status?.key_configured, query.dataUpdatedAt, key, loadModels])

  function changeKey(value: string) {
    modelRequest.current += 1
    setKey(value)
    setCatalog(null)
    setModelError("")
    setLoadingModels(false)
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!status?.can_edit || saving || !allModelsAvailable || loadingModels) return
    setSaving(true)
    setError("")
    try {
      const result = await post<AIStatus>("/api/settings/ai", {
        ...Object.fromEntries(roles.map(({ name }) => [name, models[name].trim()])),
        api_key: key.trim(),
      })
      client.setQueryData(queryKey, result)
      setKey("")
      setDraft(null)
      toast.success("AI settings saved. Ready for your next run.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save AI settings. Try again.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="profile-section">
      <CardHeader className="profile-section-header">
        <div className="flex items-start gap-3">
          <span className="profile-section-icon"><Cpu className="size-4" /></span>
          <div className="min-w-0">
            <CardTitle>AI &amp; models</CardTitle>
            <CardDescription className="mt-1 leading-5">Choose the models behind your creative team. Shared across this workspace.</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="profile-section-content">
        {query.isPending ? <Skeleton className="h-72 w-full" /> : !status ? (
          <div className="profile-ai-error" role="alert"><p>AI settings could not be loaded.</p><Button onClick={() => query.refetch()}>Try again</Button></div>
        ) : (
          <form className="profile-ai-form" onSubmit={save}>
            <div className="profile-ai-key-status">
              <ShieldCheck aria-hidden="true" />
              <div><strong>{status.key_error ? "Saved key needs attention" : status.key_configured ? "OpenAI key configured" : "Connect OpenAI"}</strong>
                <p>{status.key_error ? "The saved key could not be read. Enter it again to reconnect." : status.key_source === "saved" ? "Using your encrypted workspace key." : "Add your API key to power carousel creation."}</p>
              </div>
            </div>
            {!status.can_edit && <p className="profile-ai-note">An administrator can update these workspace settings.</p>}
            <fieldset disabled={!status.can_edit || saving} className="profile-ai-fields">
              <div className="profile-field">
                <label htmlFor="openai-api-key" className="flex items-center gap-2"><KeyRound className="size-3.5" /> OpenAI API key</label>
                <Input id="openai-api-key" type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="none" maxLength={512}
                  disabled={!status.secrets_ready} value={key} onChange={(event) => changeKey(event.target.value)}
                  placeholder={status.key_configured ? "Paste a replacement key" : "sk-..."} aria-describedby="openai-key-help" />
                <p id="openai-key-help">{status.secrets_ready ? "Stored encrypted. Leave blank to keep your saved key. Keys in .env are never used." : "The server needs credential encryption configured before you can save a key."}</p>
              </div>
              <div className="profile-ai-model-heading"><h3>A model for every role</h3>
                <Button type="button" size="sm" variant="ghost" onClick={() => void loadModels()}
                  disabled={loadingModels || (!key.trim() && !status.key_configured)}>
                  <RefreshCw className={loadingModels ? "animate-spin motion-reduce:animate-none" : ""} />
                  {loadingModels ? "Loading models..." : key.trim() ? "Load models" : "Refresh models"}
                </Button>
              </div>
              <div aria-live="polite">
                {modelError ? <p className="profile-ai-error" role="alert">{modelError}</p> :
                  <p className="profile-ai-note">{loadingModels ? "Checking which models this key can access..." : catalog ?
                    `${catalog.text_models.length} text ${catalog.text_models.length === 1 ? "model" : "models"} and ${catalog.image_models.length} image ${catalog.image_models.length === 1 ? "model" : "models"} available to this key.` :
                    key.trim() ? "Load models for this key, then choose a model for each role." : "Add your API key to load available models."}</p>}
              </div>
              <div className="profile-fields profile-ai-models">
                {roles.map(({ name, label, description }) => {
                  const options = catalog?.[name === "image_model" ? "image_models" : "text_models"] ?? []
                  const available = options.includes(models[name])
                  return (
                  <div className="profile-field" key={name}>
                    <label htmlFor={name}>{label}</label>
                    <div className="profile-model-select">
                      <select id={name} value={available ? models[name] : ""} required disabled={!catalog || loadingModels || !options.length}
                        onChange={(event) => setDraft({ ...Object.fromEntries(roles.map(({ name: field }) => [field, models[field]])) as Models, [name]: event.target.value })}
                        aria-describedby={`${name}-help`}>
                        <option value="" disabled>{!catalog ? "Load models first" : !options.length ? "No models available" : "Choose an available model"}</option>
                        {options.map(model => <option key={model} value={model}>{model.replace(/^openai\//, "")}</option>)}
                      </select>
                      <ChevronDown aria-hidden="true" />
                    </div>
                    <p id={`${name}-help`}>{description}</p>
                    {catalog && !available && <p className="profile-ai-unavailable">{!options.length ?
                      "This key has no models for this role. Check its OpenAI model access." :
                      `${models[name].replace(/^openai\//, "")} is not listed for this key. Choose another model.`}</p>}
                  </div>
                )})}
              </div>
            </fieldset>
            {error && <p className="profile-ai-error" role="alert">{error}</p>}
            <div className="profile-save-row">
              <span>Applies to new and resumed runs. Active runs keep their settings.</span>
              <Button type="submit" variant="brand" disabled={!status.can_edit || saving || !changed || !allModelsAvailable || loadingModels}>{saving ? "Saving..." : "Save AI settings"}</Button>
            </div>
            <p className="profile-ai-note">Models are loaded from OpenAI using your key. Saving rechecks availability without generating content. Some models may not support every carousel task.</p>
          </form>
        )}
      </CardContent>
    </Card>
  )
}
