import { StudioEmblem } from "@/components/layout/studio-emblem"
import { UserAvatar } from "@/components/layout/user-avatar"
import { useProfile } from "@/hooks/use-profile"

/** Show the actual submitted brief while creation or the first snapshot is pending. */
export function LaunchHandoff({ prompt, designName, accepted = false }: {
  prompt: string
  designName?: string
  accepted?: boolean
}) {
  const { profile } = useProfile()
  return (
    <div className="studio-launch-handoff">
      <div className="flex justify-end gap-3">
        <div className="studio-chat-user studio-launch-message max-w-[82%] rounded-[16px] px-4 py-3 text-sm leading-6">{prompt}</div>
        <UserAvatar src={profile.avatarUrl} name={profile.displayName} seed={profile.email} className="mt-1 size-8 shrink-0 text-[11px]" />
      </div>
      <div className="studio-launch-status" role="status" aria-live="polite">
        <StudioEmblem />
        <div className="min-w-0">
          <p className="text-sm font-semibold">{accepted ? "Opening your chat" : "Starting your carousel"}<span className="studio-launch-dots" aria-hidden="true"><i /><i /><i /></span></p>
          <p className="mt-1 text-xs leading-5 text-[var(--muted-foreground)]">{accepted ? "Your task is created. Loading its activity…" : "Creating a task with your selected design…"}</p>
          {designName && <span className="studio-launch-design">Design: {designName}</span>}
        </div>
      </div>
    </div>
  )
}
