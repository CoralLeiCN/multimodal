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
