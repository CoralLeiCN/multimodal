export type Brand = {
  id: string
  brand_id: string
  version: number
  name: string
  description: string
  colors: string
  preserve: string
  avoid: string
  reference_asset_ids: string[]
}
export type Asset = { id: string; url: string; width: number; height: number }
export type Evaluation = {
  subject_score: number
  brand_score: number
  request_score: number
  summary: string
  action: "accept" | "revise"
}
export type Run = {
  id: string
  status: string
  stage: string
  prompt: string
  brand_version: string
  request: {
    aspect_ratio: string
    candidate_count: number
    subject_strength: string
    style_strength: string
  }
  question: string | null
  error_code: string | null
  review_status: string | null
  artifacts: (Asset & { evaluation: Evaluation | null })[]
  trace_id: string | null
}
export type Status = {
  enabled: boolean
  authenticated: boolean
  ready: boolean
  message: string
  missing?: string[]
}
export type RunEvent = { sequence: number; kind: string; summary: string }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/agent${path}`, { credentials: "same-origin", ...init })
  const body = await response.json()
  if (!response.ok) throw new Error(body.message || "The request could not be completed.")
  return body as T
}
export function post<T>(path: string, body: unknown, headers: Record<string, string> = {}) {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  })
}
export async function upload(file: File, kind: "reference" | "subject") {
  if (file.size > 10 * 1024 * 1024) throw new Error("Images must be at most 10 MB.")
  const form = new FormData()
  form.append("file", file)
  form.append("kind", kind)
  return api<Asset>("/assets", { method: "POST", body: form })
}
