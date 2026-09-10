import { useQuery } from "@tanstack/react-query"
import { get } from "@/lib/api"
import type { Meta } from "@/lib/types"

export type InstagramConnection = {
  status: "checking" | "connected" | "disconnected" | "error"
  retry: () => void
}

/** UI review eligibility uses the same account list as the creation screen. */
export function useInstagramConnection(enabled: boolean): InstagramConnection {
  const meta = useQuery({
    queryKey: ["meta"],
    queryFn: () => get<Meta>("/api/meta"),
    enabled,
    refetchInterval: enabled ? 15_000 : false,
  })
  return {
    status: meta.isError ? "error"
      : !meta.data ? "checking"
        : meta.data.accounts.some(account => !account.needs_reconnect)
          ? "connected" : "disconnected",
    retry: () => { void meta.refetch() },
  }
}
