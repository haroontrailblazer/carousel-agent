import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// The console is served from "/" by FastAPI in production (the ADK dev UI was
// moved to /dev precisely so this could own the root), so no base prefix is
// needed and the router needs no basename.
//
// Tailwind is wired through its Vite plugin rather than PostCSS: there is no
// framework here that owns the PostCSS pipeline, and the plugin is the native
// v4 path.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    // web_app.py serves this directory; see SPA_DIST there.
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
  },
  server: {
    // Listen on the LAN so a 4:5 Instagram slide can be checked on a phone -
    // judging a portrait carousel on a desktop monitor is not the same thing.
    host: true,
    port: 5173,
    proxy: {
      // Same-origin in dev as in production, so the API client needs no base
      // URL logic and cookies behave identically.
      //
      // Note: no compression is configured anywhere in this proxy. Gzip
      // buffers, and buffering turns the live run trace into a single burst
      // when the run finishes - the same class of bug the backend avoids by
      // keeping its auth layer raw ASGI.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/review-api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/dev": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
})
