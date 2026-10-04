import { expect, test } from "@playwright/test"

test("search artifact exposes no agent features or API requests", async ({page}) => {
  const requests: string[] = []
  page.on("request", request => requests.push(request.url()))
  await page.route("**/api/v1/status", route => route.fulfill({json: {status: "empty", indexed_images: 0, search_available: false, text_search_available: false}}))
  await page.goto("/")
  await expect(page.getByRole("heading", {name: "Science Museum Group datasets."})).toBeVisible()
  await expect(page.getByRole("button", {name: /Open collection (chat|companion)/})).toHaveCount(0)
  await expect(page.getByRole("link", {name: "Image Studio"})).toHaveCount(0)
  expect(requests.some(url => /\/api\/v1\/(agent|explorer)/.test(url))).toBe(false)
  const response = await page.goto("/create")
  expect(response?.status()).toBe(404)
})

test("discloses sample coverage and loads browsing while search is being checked", async ({page}) => {
  const status = {status: "checking", index_version: "fixture", indexed_images: 100, sample: true, search_available: false, text_search_available: false, message: "Search is being prepared. You can browse the collection now."}
  await page.route("**/api/v1/status", route => route.fulfill({json: status}))
  await page.route("**/api/v1/filters", route => route.fulfill({json: {index_version: "fixture", places: ["London"], categories: [], date_min: null, date_max: null}}))
  await page.route("**/api/v1/images?*", route => route.fulfill({json: {index_version: "fixture", indexed_images: 100, matching_images: 100, items: [{image_id: "fixture", title: "Brass microscope", image_url: "/fixture.svg", width: 100, height: 100, associations: []}], next_cursor: null}}))
  await page.route("**/fixture.svg", route => route.fulfill({contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>'}))
  await page.goto("/")
  await expect(page.getByText("Sample · 100 images", {exact: true})).toBeVisible()
  await expect(page.getByRole("button", {name: "View Brass microscope"})).toBeVisible()
  await expect(page.getByRole("status")).toContainText("You can browse")
  await expect(page.getByRole("button", {name: "Find images similar to Brass microscope"})).toBeDisabled()
  status.sample = false
  await page.reload()
  await expect(page.getByText("100 images", {exact: true})).toBeVisible()
  await expect(page.getByText("Sample · 100 images", {exact: true})).toHaveCount(0)
})
