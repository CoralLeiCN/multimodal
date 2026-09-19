import { expect, test } from "@playwright/test"
import { mockChat } from "./chat-fixture"

const image = {
  image_id: "11111111-1111-5111-8111-111111111111",
  title: "Brass microscope",
  image_url: "/api/v1/images/11111111-1111-5111-8111-111111111111/file",
  width: 100, height: 100, score: null,
  associations: [{ record_uid: "co123", image_uid: "i123", title: "Brass microscope", description: "A microscope for exploring the smallest details.", date: "1850", maker: "Example maker", catalogue_identifiers: "001", licence: "CC BY-NC-SA 4.0", copyright: "Museum", credit: "Science Museum Group", source_url: "https://collection.sciencemuseumgroup.org.uk/objects/co123" }],
}
const image2 = { ...image, image_id: "22222222-2222-5222-8222-222222222222", title: "Early computer" }
const status = { status: "ready", index_version: "test", indexed_images: 2, sample: true, search_available: true, text_search_available: true, message: "Ready" }

test.beforeEach(async ({ page }) => {
  await mockChat(page)
  await page.route("**/api/v1/status", (route) => route.fulfill({ json: status }))
  await page.route("**/api/v1/filters", (route) => route.fulfill({ json: { index_version: "test", places: ["London", "Paris"], categories: ["Computing", "Optics"], date_min: 1850, date_max: 1950 } }))
  await page.route("**/api/v1/images?*", (route) => route.fulfill({ json: { index_version: "test", indexed_images: 2, items: [image, image2], next_cursor: null } }))
  await page.route("**/api/v1/images/*/file", (route) => route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#c7bd9e"/></svg>' }))
})

test("applies metadata filters to browsing and searches, validates years, and clears them", async ({ page }) => {
  const searches: Record<string, unknown>[] = []
  const browses: string[] = []
  await page.route("**/api/v1/images?*", async (route) => {
    browses.push(route.request().url())
    const filtered = new URL(route.request().url()).searchParams.has("place")
    await route.fulfill({ json: { index_version: "test", indexed_images: 2, matching_images: filtered ? 1 : 2, items: filtered ? [image] : [image, image2], next_cursor: null } })
  })
  await page.route("**/api/v1/search/text", async (route) => {
    searches.push(route.request().postDataJSON())
    await route.fulfill({ json: { index_version: "test", indexed_images: 2, duration_ms: 10, results: [image] } })
  })
  await page.goto("/")
  await page.getByLabel("From year").fill("1900")
  await page.getByLabel("To year").fill("1800")
  await expect(page.getByRole("button", { name: "Apply filters" })).toBeDisabled()
  await expect(page.getByRole("alert")).toContainText("starting year")
  await page.getByLabel("From year").fill("1850")
  await page.getByLabel("To year").fill("1870")
  await page.getByLabel("Place", { exact: true }).selectOption("London")
  await page.getByLabel("Category", { exact: true }).selectOption("Optics")
  await page.getByRole("button", { name: "Apply filters" }).click()
  await expect(page.getByTestId("image-card")).toHaveCount(1)
  await expect(page.getByText("1 objects match your filters")).toBeVisible()
  const browse = new URL(browses.at(-1) as string)
  expect(browse.searchParams.get("date_from")).toBe("1850")
  expect(browse.searchParams.getAll("place")).toEqual(["London"])
  await page.getByLabel("Describe an object").fill("microscope")
  await page.getByRole("button", { name: "Explore", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Your discoveries" })).toBeVisible()
  expect(searches.at(-1)?.filters).toEqual({ date_from: 1850, date_to: 1870, place: ["London"], category: ["Optics"] })
  await page.getByRole("button", { name: "Clear filters" }).click()
  await expect.poll(() => searches.length).toBe(2)
  expect(searches.at(-1)?.filters).toEqual({})
  await expect(page.getByLabel("From year")).toHaveValue("")
  await expect(page.getByLabel("Place", { exact: true })).toHaveValue("")
})

test("keeps filters for image upload and find similar", async ({ page }) => {
  let multipart = ""
  let similarFilters: unknown
  await page.route("**/api/v1/search/image", async (route) => {
    multipart = route.request().postData() || ""
    await route.fulfill({ json: { index_version: "test", indexed_images: 2, duration_ms: 10, results: [image] } })
  })
  await page.route("**/api/v1/images/*/similar", async (route) => {
    similarFilters = route.request().postDataJSON().filters
    await route.fulfill({ json: { index_version: "test", indexed_images: 2, duration_ms: 10, results: [image2] } })
  })
  await page.goto("/")
  await page.getByLabel("Place", { exact: true }).selectOption("London")
  await page.getByRole("button", { name: "Apply filters" }).click()
  await page.getByRole("tab", { name: "Search with an image" }).click()
  await page.locator('input[type="file"]').setInputFiles({ name: "query.png", mimeType: "image/png", buffer: Buffer.from("fixture") })
  await page.getByRole("button", { name: "Explore", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Your discoveries" })).toBeVisible()
  expect(multipart).toContain('name="place"')
  expect(multipart).toContain("London")
  await page.getByRole("button", { name: "Find images similar to Brass microscope" }).click()
  await expect.poll(() => similarFilters).toEqual({ place: ["London"] })
})

test("browses, searches through the dedicated API, and opens attribution", async ({ page }) => {
  let received = ""
  await page.route("**/api/v1/search/text", async (route) => {
    received = route.request().postDataJSON().query
    await route.fulfill({ json: { index_version: "test", indexed_images: 2, duration_ms: 10, results: [{ ...image, score: 0.9 }] } })
  })
  await page.goto("/")
  await expect(page.getByTestId("image-card")).toHaveCount(2)
  await page.getByLabel("Describe an object").fill("brass microscope")
  await page.getByRole("button", { name: "Explore", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Your discoveries" })).toBeVisible()
  expect(received).toBe("brass microscope")
  await expect(page.getByTestId("image-card")).toHaveCount(1)
  await page.getByRole("button", { name: "View Brass microscope" }).click()
  await expect(page.getByRole("dialog")).toContainText("CC BY-NC-SA 4.0")
  await expect(page.getByRole("link", { name: "View collection record" })).toBeVisible()
  await page.keyboard.press("Escape")
  await page.getByRole("button", { name: "Back to collection" }).click()
  await expect(page.getByTestId("image-card")).toHaveCount(2)
})

test("uploads images and shows backend errors without discarding results", async ({ page }) => {
  let uploadType = ""
  await page.route("**/api/v1/search/image", async (route) => {
    uploadType = route.request().headers()["content-type"]
    await route.fulfill({ status: 429, json: { code: "embedding_quota", message: "Gemini quota is temporarily exhausted. Try again later." } })
  })
  await page.goto("/")
  await page.getByRole("tab", { name: "Search with an image" }).click()
  await page.locator('input[type="file"]').setInputFiles({ name: "query.png", mimeType: "image/png", buffer: Buffer.from("fixture") })
  await page.getByRole("button", { name: "Explore", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("quota")
  expect(uploadType).toContain("multipart/form-data")
  await expect(page.getByTestId("image-card")).toHaveCount(2)
})

test("finds similar images and fits a phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route("**/api/v1/images/*/similar", (route) => route.fulfill({ json: { index_version: "test", indexed_images: 2, duration_ms: 5, results: [image2] } }))
  await page.goto("/")
  await page.getByRole("button", { name: "Find images similar to Brass microscope" }).click()
  await expect(page.getByRole("heading", { name: "Your discoveries" })).toBeVisible()
  await expect(page.getByTestId("image-card")).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

for (const width of [1280, 390]) {
  test(`connected chat preserves drafts and messages at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    const chatRequests: string[] = []
    page.on("request", (request) => {
      if (request.method() === "POST") chatRequests.push(request.url())
    })
    await page.goto("/")
    const toggle = page.locator('button[aria-controls="collection-chat"]')
    await toggle.click()
    const dialog = page.getByRole("complementary", { name: "Collection companion" })
    const composer = page.getByLabel("Message the collection companion")
    await expect(dialog).toBeVisible()
    await expect(composer).toBeFocused()
    await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled()
    await composer.fill("Tell me about early computers")
    await expect(composer).toHaveValue("Tell me about early computers")
    await composer.press("Shift+Enter")
    await composer.pressSequentially("And their inventors")
    await expect(page.getByRole("log")).toBeEmpty()
    await page.keyboard.press("Escape")
    await expect(dialog).not.toBeVisible()
    await expect(toggle).toBeFocused()
    await toggle.click()
    await expect(composer).toHaveValue("Tell me about early computers\nAnd their inventors")
    await composer.press("Enter")
    await expect(page.getByRole("log")).toContainText("And their inventors")
    await expect(page.getByRole("log")).toContainText("Here is your brand image.")
    await expect(composer).toHaveValue("")
    await page.getByRole("button", { name: "Close collection chat" }).click()
    await toggle.click()
    await expect(page.getByRole("log")).toContainText("And their inventors")
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await expect.poll(async () => (await dialog.boundingBox())?.x).toBe(0)
    const box = await dialog.boundingBox()
    expect(box?.width).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `/tmp/collection-chat-${width}.png` })
    await page.getByRole("button", { name: "New chat" }).click()
    await expect(page.getByRole("log")).toBeEmpty()
    await expect(composer).toBeFocused()
    await expect(page.getByLabel("Brand", { exact: true })).toHaveValue("brand-1")
    expect(chatRequests.some((url) => url.endsWith("/messages"))).toBe(true)
  })
}

for (const width of [1280, 390]) {
  test(`browses and searches while chat stays open at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.route("**/api/v1/search/text", (route) => route.fulfill({ json: { index_version: "test", indexed_images: 2, duration_ms: 10, results: [image] } }))
    await page.goto("/")
    const toggle = page.locator('button[aria-controls="collection-chat"]')
    await toggle.click()
    const panel = page.getByRole("complementary", { name: "Collection companion" })
    const composer = page.getByLabel("Message the collection companion")
    await composer.fill("Tell me about this object")
    await page.getByRole("button", { name: "View Brass microscope" }).click()
    await expect(page.getByRole("dialog")).toContainText("CC BY-NC-SA 4.0")
    await page.keyboard.press("Escape")
    await expect(panel).toBeVisible()
    await expect(toggle).toHaveAttribute("aria-expanded", "true")
    await expect(composer).toHaveValue("Tell me about this object")
    await page.getByLabel("Describe an object").fill("microscope")
    await page.getByRole("button", { name: "Explore", exact: true }).click()
    await expect(page.getByTestId("image-card")).toHaveCount(1)
    await expect(panel).toBeVisible()
    await composer.press("Enter")
    await expect(page.getByRole("log")).toContainText("Tell me about this object")
    await page.getByTestId("image-card").scrollIntoViewIfNeeded()
    const chatBox = await panel.boundingBox()
    const imageBox = await page.getByTestId("image-card").boundingBox()
    if (width > 900) expect(imageBox!.x).toBeGreaterThanOrEqual(chatBox!.x + chatBox!.width)
    else expect(imageBox!.y).toBeLessThan(chatBox!.y)
    expect(await composer.isVisible()).toBe(true)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/collection-chat-split-${width}.png` })
    await toggle.click()
    await expect(panel).not.toBeVisible()
    await expect(toggle).toHaveAttribute("aria-expanded", "false")
  })
}

test("sends the selected search image ID to the chat backend", async ({ page }) => {
  const mock = await mockChat(page)
  await page.goto("/")
  await page.getByRole("button", { name: "Use Brass microscope in chat" }).click()
  await expect(page.getByLabel("Message the collection companion")).toHaveValue(`Use image ID ${image.image_id} to create an image in my brand style.`)
  await expect(page.getByLabel("Brand", { exact: true })).toHaveValue("brand-1")
  await page.getByRole("button", { name: "Send message" }).click()
  await expect.poll(() => mock.sent.length).toBe(1)
  expect(mock.sent[0].content).toContain(image.image_id)
})
