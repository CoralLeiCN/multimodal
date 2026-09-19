import type { Page } from "@playwright/test"

export const brand = { id: "brand-1", name: "Museum Studio", version: 1 }
export async function mockChat(page: Page, options: { authenticated?: boolean; pending?: boolean; loseResponse?: boolean; disabled?: boolean } = {}) {
  let authenticated = options.authenticated ?? true
  let reads = 0
  let lost = false
  let cancelled = false
  let failed = false
  const sent: { content: string; subject_asset_id?: string; key: string }[] = []
  const submissions: string[] = []
  const chats: { id: string; title: string; brand_version: string }[] = []
  const asset = { id: "generated-1", url: "/api/v1/agent/assets/generated-1/file", width: 100, height: 100 }
  function history() {
    const done = !options.pending && ++reads > 1
    const state = cancelled ? "cancelled" : failed ? "failed" : done ? "succeeded" : "chat_queued"
    return { ...chats[0], assets: done ? [asset] : [], messages: sent.flatMap((item, index) => {
      const user = { id: `user-${index}`, role: "user", content: item.content, asset_ids: [], run_id: `run-${index}`, run_status: state, error_code: failed ? "source_unavailable" : null }
      return done && !cancelled && !failed ? [user, { ...user, id: `assistant-${index}`, role: "assistant", content: "Here is your brand image.", asset_ids: [asset.id] }] : [user]
    }) }
  }
  await page.route("**/api/v1/agent/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace("/api/v1/agent", "")
    if (path === "/status") return route.fulfill({ json: { enabled: !options.disabled, authenticated, ready: authenticated, message: options.disabled ? "Image creation is not configured yet." : "Ready" } })
    if (path === "/session") { authenticated = true; return route.fulfill({ json: { authenticated } }) }
    if (!authenticated) return route.fulfill({ status: 401, json: { message: "Sign in again." } })
    if (path === "/brands") return route.fulfill({ json: [brand] })
    if (path === "/conversations") {
      if (request.method() === "POST") { chats.unshift({ id: "chat-1", ...request.postDataJSON() }); return route.fulfill({ status: 201, json: chats[0] }) }
      return route.fulfill({ json: chats })
    }
    if (path.endsWith("/messages")) {
      const key = request.headers()["idempotency-key"]
      submissions.push(key)
      if (!sent.some((item) => item.key === key)) sent.push({ ...request.postDataJSON(), key })
      if (options.loseResponse && !lost) { lost = true; return route.abort("failed") }
      return route.fulfill({ status: 202, json: { message_id: "user-0", run: { id: "run-0", status: "chat_queued" } } })
    }
    if (path.endsWith("/cancel")) { cancelled = true; return route.fulfill({ json: { status: "cancelled" } }) }
    if (path === "/conversations/chat-1") return route.fulfill({ json: history() })
    if (path.endsWith("/file")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>' })
    return route.fulfill({ status: 404, json: { message: "Unknown request" } })
  })
  return { sent, submissions, fail: () => { failed = true } }
}
