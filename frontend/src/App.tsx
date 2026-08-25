import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "react-router"
import { Toaster } from "sonner"

import { AuthProvider } from "@/hooks/use-auth"
import { router } from "@/router"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The console shows live agent runs; a stale snapshot is misleading in a
      // way that a stale product listing is not.
      staleTime: 5_000,
      retry: (failureCount, error) => {
        // Never retry an auth failure: a redirect is already in flight, and
        // retrying just delays it while firing more doomed requests.
        const status = (error as { status?: number })?.status
        if (status === 401 || status === 403 || status === 404) return false
        return failureCount < 2
      },
      refetchOnWindowFocus: true,
    },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
        {/* Top-centre: a carousel takes ~15 minutes, so the confirmation
            that one started is worth putting where the eye already is
            rather than in a corner. */}
        <Toaster
          position="top-center"
          toastOptions={{
            style: {
              background: "var(--card)",
              color: "var(--foreground)",
              border: "1px solid var(--border)",
            },
          }}
        />
      </AuthProvider>
    </QueryClientProvider>
  )
}
