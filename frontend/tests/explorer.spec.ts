import { expect, test, type Page } from "@playwright/test"

const image = { image_id: "11111111-1111-5111-8111-111111111111", title: "Brass microscope", image_url: "/api/v1/images/fixture/file", width: 100, height: 100,
  associations: [{ record_uid: "co123", image_uid: "i1", title: "Microscope", description: "An optical instrument", date: "1850", maker: "Museum maker", places: ["London"], categories: ["Optics"], licence: "CC BY 4.0", credit: "Science Museum Group", copyright: "Museum", source_url: "https://collection.sciencemuseumgroup.org.uk/objects/co123" }] }

export async function mockExplorer(page: Page, options: { signedOut?: boolean; pending?: boolean; loseResponse?: boolean } = {}) {
  let lost = false
  let cancelled = false
  const submissions: { key: string; content: string; upload_id?: string }[] = []
  const conversations: {id: string; title: string}[] = []
  const runs: {id: string; content: string; status: string; answer: string; results: typeof image[]; error: string | null}[] = []
  await page.route("**/api/v1/status", route => route.fulfill({json: {status: "ready", index_version: "test", indexed_images: 1, search_available: true, text_search_available: true}}))
  await page.route("**/api/v1/filters", route => route.fulfill({json: {index_version: "test", places: ["London"], categories: ["Optics"]}}))
  await page.route("**/api/v1/images?*", route => route.fulfill({json: {index_version: "test", indexed_images: 1, matching_images: 1, items: [image], next_cursor: null}}))
  await page.route("**/api/v1/images/*/file", route => route.fulfill({contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#ab9a75"/></svg>'}))
  await page.route("**/api/v1/explorer/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1/explorer", "")
    if (path === "/status") return route.fulfill({json: {authenticated: !options.signedOut, ready: true, auth_ready: true}})
    if (path === "/conversations") {
      if (route.request().method() === "POST") conversations.push({id: "chat-1", title: "Microscopes"})
      return route.fulfill({json: route.request().method() === "POST" ? conversations[0] : conversations})
    }
    if (path === "/uploads") return route.fulfill({status: 201, json: {id: "upload-1"}})
    if (path.endsWith("/messages")) {
      const key = route.request().headers()["idempotency-key"]
      const body = route.request().postDataJSON()
      if (!submissions.some(s => s.key === key)) runs.push({id: `run-${runs.length}`, content: body.content, status: options.pending ? "running" : "succeeded", answer: options.pending ? "" : "The catalogue identifies this as a microscope.", results: options.pending ? [] : [image], error: null})
      submissions.push({key, ...body})
      if (options.loseResponse && !lost) { lost = true; return route.abort("failed") }
      return route.fulfill({status: 202, json: runs.at(-1)})
    }
    if (path.endsWith("/cancel")) { cancelled = true; runs.at(-1)!.status = "cancelled"; runs.at(-1)!.error = "cancelled"; return route.fulfill({json: {status: "cancelled"}}) }
    if (path.endsWith("/events")) return route.fulfill({contentType: "text/event-stream", body: `id: 1\nevent: tool\ndata: {"name":"lookup_record"}\n\n${cancelled ? 'id: 2\nevent: status\ndata: {"status":"cancelled"}\n\n' : ''}`})
    if (path === "/conversations/chat-1") return route.fulfill({json: {...conversations[0], runs}})
    return route.fulfill({status: 404, json: {detail: "Not found"}})
  })
  return { submissions }
}

test("sign-in is required and no image generation controls appear", async ({page}) => {
  await mockExplorer(page, {signedOut: true})
  await page.goto("/")
  await page.getByRole("button", {name: "Open collection companion"}).click()
  await expect(page.getByRole("link", {name: "Sign in with Hugging Face"})).toHaveAttribute("href", "/api/v1/explorer/auth/login")
  await expect(page.getByRole("link", {name: "Image Studio"})).toHaveCount(0)
  await expect(page.getByLabel("Brand", {exact: true})).toHaveCount(0)
})

test("explores a collection ID, renders evidence, and restores a follow-up conversation", async ({page}) => {
  const mock = await mockExplorer(page)
  await page.goto("/")
  await page.getByRole("button", {name: "View Brass microscope"}).click()
  await page.getByRole("button", {name: "Chat about collection ID co123"}).click()
  await expect(page.getByLabel("Ask about the collection")).toHaveValue("Explore collection record co123")
  await page.getByRole("button", {name: "Send exploration message"}).click()
  await expect(page.getByRole("log")).toContainText("catalogue identifies")
  await expect(page.getByRole("log").getByRole("link", {name: "co123"})).toHaveAttribute("href", image.associations[0].source_url)
  await page.reload()
  await page.getByRole("button", {name: "Open collection companion"}).click()
  await page.getByLabel("Collection conversation", {exact: true}).selectOption("chat-1")
  await expect(page.getByRole("log")).toContainText("catalogue identifies")
  await page.getByRole("button", {name: "Explore this image"}).click()
  await page.getByRole("button", {name: "Send exploration message"}).click()
  expect(mock.submissions[1].content).toContain(image.image_id)
})

test("retries with the same idempotency key and cancels a pending turn", async ({page}) => {
  const mock = await mockExplorer(page, {pending: true, loseResponse: true})
  await page.goto("/")
  await page.getByRole("button", {name: "Open collection companion"}).click()
  await page.getByLabel("Ask about the collection").fill("Find cameras")
  await page.getByRole("button", {name: "Send exploration message"}).click()
  await page.getByRole("button", {name: "Retry message"}).click()
  await expect.poll(() => mock.submissions.length).toBe(2)
  expect(mock.submissions[0].key).toBe(mock.submissions[1].key)
  await page.getByRole("button", {name: "Stop exploration"}).click()
  await expect(page.getByRole("log")).toContainText("Exploration cancelled")
})

for (const status of [409, 422]) {
  test(`unlocks the draft and conversation controls after HTTP ${status}`, async ({page}) => {
    const mock = await mockExplorer(page)
    const endpoint = "**/api/v1/explorer/conversations/*/messages"
    const message = status === 409
      ? "Start a new conversation to continue exploring."
      : "Check the message and try again."
    let rejectedKey = ""
    await page.route(endpoint, route => {
      rejectedKey = route.request().headers()["idempotency-key"]
      return route.fulfill({status, json: {detail: message}})
    })
    await page.goto("/")
    await page.getByRole("button", {name: "Open collection companion"}).click()
    await page.getByLabel("Ask about the collection").fill("Find cameras")
    await page.getByRole("button", {name: "Send exploration message"}).click()
    await expect(page.getByRole("alert")).toHaveText(message)
    await expect(page.getByLabel("Ask about the collection")).toBeEnabled()
    await expect(page.getByLabel("Ask about the collection")).toHaveValue("Find cameras")
    await expect(page.getByLabel("Collection conversation", {exact: true})).toBeEnabled()
    await expect(page.getByRole("button", {name: "New collection conversation"})).toBeEnabled()
    await expect(page.getByRole("button", {name: "Retry message"})).toHaveCount(0)
    if (status === 409) {
      await page.getByRole("button", {name: "New collection conversation"}).click()
      await expect(page.getByLabel("Collection conversation", {exact: true})).toHaveValue("")
      await expect(page.getByLabel("Ask about the collection")).toHaveValue("")
    } else {
      await page.unroute(endpoint)
      await page.getByLabel("Ask about the collection").fill("Find microscopes")
      await page.getByRole("button", {name: "Send exploration message"}).click()
      await expect(page.getByRole("log")).toContainText("catalogue identifies")
      expect(mock.submissions[0].content).toBe("Find microscopes")
      expect(mock.submissions[0].key).not.toBe(rejectedKey)
    }
  })
}

test("preserves the retry key after a server error", async ({page}) => {
  const mock = await mockExplorer(page)
  const endpoint = "**/api/v1/explorer/conversations/*/messages"
  let uncertainKey = ""
  await page.route(endpoint, route => {
    uncertainKey = route.request().headers()["idempotency-key"]
    return route.fulfill({status: 503, json: {detail: "Temporarily unavailable."}})
  })
  await page.goto("/")
  await page.getByRole("button", {name: "Open collection companion"}).click()
  await page.getByLabel("Ask about the collection").fill("Find cameras")
  await page.getByRole("button", {name: "Send exploration message"}).click()
  await expect(page.getByRole("button", {name: "Retry message"})).toBeVisible()
  await expect(page.getByLabel("Ask about the collection")).toBeDisabled()
  await page.unroute(endpoint)
  await page.getByRole("button", {name: "Retry message"}).click()
  await expect(page.getByRole("log")).toContainText("catalogue identifies")
  expect(mock.submissions[0].key).toBe(uncertainKey)
})

test("preserves an accepted message's retry key when history returns HTTP 404", async ({page}) => {
  const mock = await mockExplorer(page)
  await page.route("**/api/v1/explorer/conversations/chat-1", route => {
    if (mock.submissions.length === 1)
      return route.fulfill({status: 404, json: {detail: "History unavailable."}})
    return route.fallback()
  })
  await page.goto("/")
  await page.getByRole("button", {name: "Open collection companion"}).click()
  await page.getByLabel("Ask about the collection").fill("Find cameras")
  await page.getByRole("button", {name: "Send exploration message"}).click()
  await page.getByRole("button", {name: "Retry message"}).click()
  await expect(page.getByRole("log")).toContainText("catalogue identifies")
  expect(mock.submissions).toHaveLength(2)
  expect(mock.submissions[1].key).toBe(mock.submissions[0].key)
  await expect(page.getByRole("button", {name: "Retry message"})).toHaveCount(0)
})

test("accepts an image and fits the phone viewport", async ({page}) => {
  const mock = await mockExplorer(page)
  await page.setViewportSize({width: 390, height: 844})
  await page.goto("/")
  await page.getByRole("button", {name: "Open collection companion"}).click()
  await page.getByLabel("Ask about the collection").fill("Find objects like this")
  await page.getByLabel("Attach image to conversation").setInputFiles({name: "sample.png", mimeType: "image/png", buffer: Buffer.from("fixture")})
  await page.getByRole("button", {name: "Send exploration message"}).click()
  await expect(page.getByRole("log")).toContainText("catalogue identifies")
  expect(mock.submissions[0].upload_id).toBe("upload-1")
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({path: "/tmp/collection-explorer-web-mobile.png"})
})
