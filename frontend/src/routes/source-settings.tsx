import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Rss } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { get, put } from "@/lib/api"

type Sources = { rss_feeds: string[]; youtube_channels: string[] }
const key = ["settings", "sources"]
export function SourceSettingsSection() {
  const client = useQueryClient()
  const query = useQuery({ queryKey:key, queryFn:()=>get<Sources>("/api/settings/sources") })
  const [rss,setRss] = React.useState<string|null>(null)
  const [youtube,setYoutube] = React.useState<string|null>(null)
  const [busy,setBusy] = React.useState(false)
  const [error,setError] = React.useState("")
  async function save(event:React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("")
    try {
      const lines=(value:string)=>value.split("\n").map(line=>line.trim()).filter(Boolean)
      const result=await put<Sources>("/api/settings/sources",{rss_feeds:lines(rss??query.data?.rss_feeds.join("\n")??""),youtube_channels:lines(youtube??query.data?.youtube_channels.join("\n")??"")})
      client.setQueryData(key,result); setRss(null); setYoutube(null); toast.success("Newsroom sources saved")
    } catch(cause) { setError(cause instanceof Error?cause.message:"Could not save your sources.") }
    finally { setBusy(false) }
  }
  return <Card className="profile-section">
    <CardHeader className="profile-section-header"><div className="flex items-start gap-3"><span className="profile-section-icon"><Rss className="size-4"/></span><div><CardTitle>Your newsroom sources</CardTitle><CardDescription className="mt-1">Choose what arrives in your personal newsroom.</CardDescription></div></div></CardHeader>
    <CardContent className="profile-section-content">
      {query.isError?<div role="alert"><p>Sources could not be loaded.</p><Button onClick={()=>query.refetch()}>Try again</Button></div>:<form onSubmit={save} className="profile-ai-form"><fieldset disabled={busy||!query.data} className="profile-ai-fields">
        <div className="profile-field"><label htmlFor="rss-sources">RSS feeds</label><textarea id="rss-sources" className="min-h-32 w-full rounded-xl border border-[var(--border)] bg-[var(--background)] p-3 text-sm" value={rss??query.data?.rss_feeds.join("\n")??""} onChange={event=>setRss(event.target.value)}/><p>One HTTPS feed URL per line. Leave empty to stop fetching RSS.</p></div>
        <div className="profile-field"><label htmlFor="youtube-sources">YouTube channels</label><textarea id="youtube-sources" className="min-h-24 w-full rounded-xl border border-[var(--border)] bg-[var(--background)] p-3 text-sm" value={youtube??query.data?.youtube_channels.join("\n")??""} onChange={event=>setYoutube(event.target.value)}/><p>One UC channel ID or youtube.com/channel/ID URL per line.</p></div>
        {error&&<p role="alert" className="profile-ai-error">{error}</p>}
        <div className="profile-save-row"><span>Only your newsroom uses these sources.</span><Button type="submit" variant="brand" disabled={rss===null&&youtube===null}>{busy?"Saving…":"Save sources"}</Button></div>
      </fieldset></form>}
    </CardContent>
  </Card>
}
