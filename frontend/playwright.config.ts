import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./tests",
  testMatch: process.env.APP_EDITION === "web" ? ["explorer.spec.ts"] : process.env.APP_EDITION === "studio" ? ["chat.spec.ts", "creation.spec.ts", "collection.spec.ts"] : ["search-edition.spec.ts"],
  fullyParallel: false,
  workers: 1,
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
})
