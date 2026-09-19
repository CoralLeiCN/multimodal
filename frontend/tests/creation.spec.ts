import { expect, test } from "@playwright/test"

const brand = { id: "brand-v1", brand_id: "brand", version: 1, name: "Fieldwork", description: "Natural editorial imagery", colors: "Green", preserve: "", avoid: "", reference_asset_ids: [] }
const run = { id: "run-1", status: "queued", stage: "queued", prompt: "A microscope in soft daylight", brand_version: brand.id, request: { aspect_ratio: "1:1", candidate_count: 2, subject_strength: "high", style_strength: "medium" }, question: null, error_code: null, review_status: null, artifacts: [], trace_id: "0123456789abcdef0123456789abcdef" }
const evaluation = { subject_score: 90, brand_score: 88, request_score: 85, summary: "Warm lighting matches the brand.", action: "accept" }
const result = { ...run, status: "succeeded", stage: "completed", review_status: "accepted", artifacts: [{ id: "output-1", url: "/api/v1/agent/assets/output-1/file", width: 100, height: 100, evaluation }] }

test("studio remains accessible when collection search and agent configuration are absent", async ({ page }) => {
  await page.route("**/api/v1/agent/status", (route) => route.fulfill({ json: { enabled: false, authenticated: false, ready: false } }))
  await page.goto("/create")
  await expect(page.getByRole("heading", { name: "Make it your own." })).toBeVisible()
  await expect(page.getByText("The studio is ready for setup.", { exact: false })).toBeVisible()
  await expect(page.getByRole("button", { name: "Generate images" })).toHaveCount(0)
})

test("sign in, save a brand, generate, review, and request an edit", async ({ page }) => {
  let authenticated = false
  let profiles: unknown[] = []
  let runs: unknown[] = []
  const submissions: Record<string, unknown>[] = []
  await page.route("**/api/v1/agent/status", (route) => route.fulfill({ json: { enabled: true, authenticated, ready: authenticated, message: "Ready" } }))
  await page.route("**/api/v1/agent/session", (route) => { authenticated = true; return route.fulfill({ json: { authenticated } }) })
  await page.route("**/api/v1/agent/brands", (route) => {
    if (route.request().method() === "POST") { profiles = [brand]; return route.fulfill({ status: 201, json: brand }) }
    return route.fulfill({ json: profiles })
  })
  await page.route("**/api/v1/agent/runs", (route) => {
    if (route.request().method() === "POST") {
      submissions.push(route.request().postDataJSON())
      expect(route.request().headers()["idempotency-key"]).toBeTruthy()
      runs = [result]
      return route.fulfill({ status: 202, json: run })
    }
    return route.fulfill({ json: runs })
  })
  await page.route("**/api/v1/agent/runs/run-1", (route) => route.fulfill({ json: result }))
  await page.route("**/api/v1/agent/runs/run-1/events", (route) => route.fulfill({ contentType: "text/event-stream", body: 'id: 1\ndata: {"sequence":1,"kind":"succeeded","summary":"Your images are ready to review."}\n\nevent: done\ndata: {}\n\n' }))
  await page.route("**/api/v1/agent/assets/*/file", (route) => route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#75835c"/></svg>' }))
  await page.goto("/create")
  await page.getByLabel("Workspace access key").fill("test-workspace-key")
  await page.getByRole("button", { name: "Sign in", exact: true }).click()
  await page.getByLabel("Brand name", { exact: true }).fill("Fieldwork")
  await page.getByLabel("Brand description").fill("Natural editorial imagery")
  await page.getByRole("button", { name: "Save brand", exact: true }).click()
  await expect(page.getByRole("combobox", { name: "Brand style", exact: true })).toHaveValue(brand.id)
  await page.getByLabel("What would you like to create?").fill(run.prompt)
  await page.getByRole("button", { name: "Generate images", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Candidate 1" })).toBeVisible()
  await expect(page.getByText(evaluation.summary)).toBeVisible()
  await page.getByRole("button", { name: "Refine this image" }).click()
  await expect(page.getByText("Editing a previous result")).toBeVisible()
  await page.getByLabel("What would you like to create?").fill("Make the background blue")
  await page.getByRole("button", { name: "Generate an edit", exact: true }).click()
  await expect.poll(() => submissions.length).toBe(2)
  expect(submissions[1].parent_run_id).toBe("run-1")
  expect(submissions[1].selected_asset_id).toBe("output-1")
})

test("cancels a queued run and reports a failed upload", async ({ page }) => {
  await page.route("**/api/v1/agent/status", (route) => route.fulfill({ json: { enabled: true, authenticated: true, ready: true } }))
  await page.route("**/api/v1/agent/brands", (route) => route.fulfill({ json: [brand] }))
  await page.route("**/api/v1/agent/runs", (route) => route.fulfill({ json: [run] }))
  await page.route("**/api/v1/agent/runs/run-1/events", (route) => route.fulfill({ contentType: "text/event-stream", body: ': heartbeat\n\n' }))
  await page.route("**/api/v1/agent/runs/run-1/cancel", (route) => route.fulfill({ json: { ...run, status: "cancelled", stage: "cancelled" } }))
  await page.route("**/api/v1/agent/assets", (route) => route.fulfill({ status: 422, json: { message: "The image could not be decoded." } }))
  await page.goto("/create")
  await page.getByLabel("Recent runs").selectOption("run-1")
  await page.getByRole("button", { name: "Cancel run" }).click()
  await expect(page.getByRole("button", { name: "Cancel run" })).toHaveCount(0)
  await page.getByLabel("Subject image", { exact: false }).setInputFiles({ name: "bad.png", mimeType: "image/png", buffer: Buffer.from("bad") })
  await expect(page.getByRole("alert")).toContainText("The image could not be decoded.")
})
