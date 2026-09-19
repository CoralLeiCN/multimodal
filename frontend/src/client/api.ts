export * from "./generated/sdk.gen"
export type { ImageRead, MetadataFilters, SearchResponse } from "./generated/types.gen"

export async function read<T>(request: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const response = await request
  if (response.data !== undefined) return response.data
  const error = response.error
  const message =
    error && typeof error === "object" && "message" in error
      ? String(error.message)
      : "We couldn't reach the collection. Please try again."
  throw new Error(message)
}
