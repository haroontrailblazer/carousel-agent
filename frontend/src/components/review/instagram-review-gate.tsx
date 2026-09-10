import { Instagram, Loader2 } from "lucide-react"
import { Link } from "react-router"
import { Button } from "@/components/ui/button"
import type { InstagramConnection } from "@/hooks/use-instagram-connection"

export function InstagramReviewGate({ connection, compact = false }: {
  connection: InstagramConnection
  compact?: boolean
}) {
  const checking = connection.status === "checking"
  const failed = connection.status === "error"
  return (
    <div className={compact ? "review-connection-gate review-connection-gate--compact" : "review-connection-gate"} role="status">
      {checking ? <Loader2 className="size-5 animate-spin" /> : <Instagram className="size-5 text-[var(--brand)]" />}
      <h3>{checking ? "Checking Instagram connection…" : failed ? "Could not check Instagram" : "Connect Instagram to review"}</h3>
      {!checking && <>
        <p>{failed ? "We couldn’t verify your connection. Try again to unlock review actions."
          : compact ? "Connect an Instagram account to send feedback to the agents."
            : "Approve and reject become available when an Instagram account is connected. You can still preview and download the carousel."}</p>
        {failed
          ? <Button onClick={connection.retry} size="sm">Try again</Button>
          : <Button variant="brand" size="sm" asChild><Link to="/profile?instagram=connect">Connect Instagram</Link></Button>}
      </>}
    </div>
  )
}
