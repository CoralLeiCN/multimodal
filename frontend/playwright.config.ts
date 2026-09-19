import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
})
