import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig(({ mode }) => {
  const edition = ["web", "studio"].includes(mode) ? mode : "search"
  return {
    resolve: {
      alias: { "@edition": resolve(import.meta.dirname, `src/editions/${edition}.tsx`) },
    },
    build: { outDir: `dist/${edition}` },
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: false,
        },
      },
    },
  }
})
