import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  ImagePlus,
  LoaderCircle,
  Search,
  Sparkles,
  X,
} from "lucide-react"
import { type FormEvent, useEffect, useRef, useState } from "react"
import {
  browseImages,
  filterOptions,
  type ImageRead,
  type MetadataFilters,
  read,
  type SearchResponse,
  searchImage,
  searchText,
  similarImages,
  status,
} from "../client/api"
import { ImageCard } from "../components/image-card"
import { ImageDetail } from "../components/image-detail"
import { Button } from "../components/ui/button"

const suggestions = [
  "Scientific instruments",
  "Early computers",
  "Cameras",
  "Illustrations",
  "Medical tools",
]

type SearchIntent =
  | { type: "text"; text: string }
  | { type: "image"; file: File }
  | { type: "similar"; image: ImageRead }

export function CollectionPage() {
  const [filters, setFilters] = useState<MetadataFilters>({})
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [place, setPlace] = useState("")
  const [category, setCategory] = useState("")
  const [lastSearch, setLastSearch] = useState<SearchIntent | null>(null)
  const health = useQuery({
    queryKey: ["status"],
    queryFn: ({ signal }) => read(status({ signal })),
    refetchInterval: 10000,
  })
  const options = useQuery({
    queryKey: ["filters", health.data?.index_version],
    queryFn: ({ signal }) => read(filterOptions({ signal })),
    enabled: (health.data?.indexed_images ?? 0) > 0,
  })
  const collection = useInfiniteQuery({
    queryKey: ["images", health.data?.index_version, filters],
    queryFn: ({ pageParam, signal }) =>
      read(browseImages({ query: { limit: 24, cursor: pageParam, ...filters }, signal })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor || undefined,
    enabled: (health.data?.indexed_images ?? 0) > 0,
  })
  const [query, setQuery] = useState("")
  const [mode, setMode] = useState<"text" | "image">("text")
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState("")
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [label, setLabel] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [detail, setDetail] = useState<ImageRead | null>(null)
  const request = useRef<AbortController | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const textInput = useRef<HTMLInputElement | null>(null)
  const available = health.data?.search_available ?? false
  const textAvailable = health.data?.text_search_available ?? false

  useEffect(() => {
    if (!file) {
      setPreview("")
      return
    }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  useEffect(() => () => request.current?.abort(), [])

  async function perform(
    action: (signal: AbortSignal) => Promise<SearchResponse>,
    nextLabel: string,
  ) {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError("")
    try {
      const response = await action(controller.signal)
      if (!controller.signal.aborted) {
        setResults(response)
        setLabel(nextLabel)
      }
    } catch (caught) {
      if (!controller.signal.aborted)
        setError(
          caught instanceof Error ? caught.message : "Search is unavailable. Please try again.",
        )
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  function searchFor(text: string) {
    setMode("text")
    setQuery(text)
    if (text.trim()) runSearch({ type: "text", text: text.trim() })
  }

  function runSearch(intent: SearchIntent, selectedFilters = filters) {
    setLastSearch(intent)
    if (intent.type === "text")
      void perform(
        (signal) =>
          read(
            searchText({
              body: { query: intent.text, limit: 24, filters: selectedFilters },
              signal,
            }),
          ),
        intent.text,
      )
    else if (intent.type === "image")
      void perform(
        (signal) =>
          read(
            searchImage({ body: { image: intent.file, limit: 24, ...selectedFilters }, signal }),
          ),
        "your image",
      )
    else
      void perform(
        (signal) =>
          read(
            similarImages({
              path: { image_id: intent.image.image_id },
              body: { limit: 24, filters: selectedFilters },
              signal,
            }),
          ),
        `Similar to ${intent.image.title}`,
      )
  }

  const invalidYear = (value: string) =>
    value !== "" &&
    (!Number.isInteger(Number(value)) || Number(value) === 0 || Math.abs(Number(value)) > 9999)
  const invalidDates =
    invalidYear(dateFrom) ||
    invalidYear(dateTo) ||
    Boolean(dateFrom && dateTo && Number(dateFrom) > Number(dateTo))
  const hasFilters =
    filters.date_from != null ||
    filters.date_to != null ||
    Boolean(filters.place?.length || filters.category?.length)

  function applyFilters(next: MetadataFilters) {
    request.current?.abort()
    setBusy(false)
    setError("")
    setResults(null)
    setFilters(next)
    if (lastSearch) runSearch(lastSearch, next)
  }

  function clearFilters() {
    setDateFrom("")
    setDateTo("")
    setPlace("")
    setCategory("")
    applyFilters({})
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (mode === "text") searchFor(query)
    else if (file) runSearch({ type: "image", file })
  }

  function findSimilar(image: ImageRead) {
    setDetail(null)
    runSearch({ type: "similar", image })
  }

  function reset() {
    request.current?.abort()
    setBusy(false)
    setResults(null)
    setError("")
    setQuery("")
    setFile(null)
    setLastSearch(null)
  }

  function chooseFile(nextFile: File | undefined) {
    if (!nextFile) return
    if (!["image/jpeg", "image/png"].includes(nextFile.type)) {
      setError("Choose a JPEG or PNG image.")
      return
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setError("Choose an image smaller than 10 MiB.")
      return
    }
    setFile(nextFile)
    setError("")
  }

  const images = results?.results ?? collection.data?.pages.flatMap((page) => page.items) ?? []
  const matchingCount =
    collection.data?.pages[0]?.matching_images ?? health.data?.indexed_images ?? 0
  const loading =
    health.isPending || ((health.data?.indexed_images ?? 0) > 0 && collection.isPending)
  const displayError = error || collection.error?.message || health.error?.message

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="/" aria-label="Collection Explorer home">
          <span className="brand-mark">
            c<span>e</span>
          </span>
          <span>
            COLLECTION
            <br />
            EXPLORER
          </span>
        </a>
        <div className="header-center">SCIENCE MUSEUM GROUP</div>
        <a
          className="header-link"
          href="https://collection.sciencemuseumgroup.org.uk/"
          target="_blank"
          rel="noreferrer"
        >
          The collection <ArrowUpRight size={16} />
        </a>
      </header>

      <main>
        <section className="hero">
          <div className="hero-top">
            <span className="eyebrow">
              <span className="tiny-dot" /> A NEW WAY TO EXPLORE
            </span>
            <span className="sample-pill">
              <span />
              {health.data?.indexed_images ?? 0} images · sample collection
            </span>
          </div>
          <div className="hero-heading">
            <h1>
              Follow your
              <br />
              <em>curiosity.</em>
            </h1>
            <p>
              A world of objects, ideas, and discoveries.
              <br />
              Describe what you’re looking for, or start with an image.
            </p>
          </div>
          <div className="search-panel">
            <div className="search-modes" role="tablist" aria-label="Search method">
              <button
                type="button"
                role="tab"
                aria-selected={mode === "text"}
                onClick={() => setMode("text")}
                className={mode === "text" ? "active" : ""}
              >
                <Search size={16} />
                Search with words
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === "image"}
                onClick={() => setMode("image")}
                className={mode === "image" ? "active" : ""}
              >
                <ImagePlus size={17} />
                Search with an image
              </button>
            </div>
            <form onSubmit={submit} className="search-form">
              {mode === "text" ? (
                <div className="query-input">
                  <Search size={23} strokeWidth={1.5} />
                  <label className="sr-only" htmlFor="search-query">
                    Describe an object
                  </label>
                  <input
                    id="search-query"
                    ref={textInput}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    maxLength={2000}
                    placeholder="Try ‘a brass microscope’ or ‘an early computer’"
                    autoComplete="off"
                  />
                </div>
              ) : (
                <div className="upload-input">
                  <input
                    ref={fileInput}
                    id="image-upload"
                    className="sr-only"
                    type="file"
                    accept="image/png,image/jpeg"
                    onChange={(event) => chooseFile(event.target.files?.[0])}
                  />
                  <label htmlFor="image-upload">
                    {preview ? (
                      <img src={preview} alt="Your query" />
                    ) : (
                      <ImagePlus size={26} strokeWidth={1.5} />
                    )}
                    <span>
                      {file ? file.name : "Choose an image to explore"}
                      <small>JPG or PNG, up to 10 MiB</small>
                    </span>
                  </label>
                  {file && (
                    <button
                      type="button"
                      className="remove-file"
                      aria-label="Remove uploaded image"
                      onClick={() => {
                        setFile(null)
                        if (fileInput.current) fileInput.current.value = ""
                      }}
                    >
                      <X size={18} />
                    </button>
                  )}
                </div>
              )}
              <Button
                type="submit"
                disabled={!textAvailable || (mode === "text" ? !query.trim() : !file)}
                className="search-submit"
              >
                {busy ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />}
                <span>Explore</span>
              </Button>
            </form>
            {mode === "image" && (
              <p className="upload-notice">
                Your image is sent to Google to generate an embedding. We use that embedding to
                find similar images in this collection. Your image is not added to the collection.
              </p>
            )}
          </div>
          <form
            className="metadata-filters"
            aria-label="Filter collection"
            onSubmit={(event) => {
              event.preventDefault()
              if (!invalidDates)
                applyFilters({
                  ...(dateFrom ? { date_from: Number(dateFrom) } : {}),
                  ...(dateTo ? { date_to: Number(dateTo) } : {}),
                  ...(place ? { place: [place] } : {}),
                  ...(category ? { category: [category] } : {}),
                })
            }}
          >
            <div className="filter-heading">
              <span className="eyebrow">REFINE YOUR DISCOVERY</span>
              {hasFilters && <span>Filters applied</span>}
            </div>
            <div className="filter-fields">
              <label htmlFor="date-from">
                From year
                <input
                  id="date-from"
                  type="number"
                  min={-9999}
                  max={9999}
                  step={1}
                  placeholder={String(options.data?.date_min ?? "Any")}
                  value={dateFrom}
                  onChange={(event) => setDateFrom(event.target.value)}
                />
              </label>
              <label htmlFor="date-to">
                To year
                <input
                  id="date-to"
                  type="number"
                  min={-9999}
                  max={9999}
                  step={1}
                  placeholder={String(options.data?.date_max ?? "Any")}
                  value={dateTo}
                  onChange={(event) => setDateTo(event.target.value)}
                />
              </label>
              <label htmlFor="place-filter">
                Place
                <select
                  id="place-filter"
                  aria-label="Place"
                  value={place}
                  onChange={(event) => setPlace(event.target.value)}
                >
                  <option value="">All places</option>
                  {options.data?.places.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor="category-filter">
                Category
                <select
                  id="category-filter"
                  aria-label="Category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                >
                  <option value="">All categories</option>
                  {options.data?.categories.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                type="submit"
                variant="outline"
                disabled={invalidDates || !health.data?.indexed_images}
              >
                Apply filters
              </Button>
              <button type="button" className="clear-filters" onClick={clearFilters}>
                Clear filters
              </button>
            </div>
            <p className="filter-help">
              Dates include overlapping creation ranges. Place refers to where an object was made.
            </p>
            {invalidDates && (
              <p className="filter-validation" role="alert">
                Enter nonzero years from -9999 to 9999, with the starting year no later than the
                ending year.
              </p>
            )}
            {options.error && (
              <p className="filter-validation" role="alert">
                Filter options could not be loaded. Please try again.
              </p>
            )}
          </form>
          <div className="suggestions">
            <span>A little inspiration</span>
            {suggestions.map((suggestion) => (
              <button
                type="button"
                key={suggestion}
                onClick={() => searchFor(suggestion)}
                disabled={!textAvailable}
              >
                {suggestion}
                <ArrowUpRight size={12} />
              </button>
            ))}
          </div>
        </section>

        <section
          className="collection-section"
          aria-label="Collection images"
          aria-busy={busy || loading}
        >
          <div className="section-heading">
            <div>
              <div className="eyebrow">
                {results ? "CONNECTIONS WORTH EXPLORING" : "FROM THE ARCHIVE"}
              </div>
              <h2>{results ? "Your discoveries" : "A small window into a big collection"}</h2>
            </div>
            <span className="results-meta">
              {busy ? (
                <>
                  <LoaderCircle className="spin" size={14} /> Finding connections…
                </>
              ) : results ? (
                `${images.length} results · ${(results.duration_ms / 1000).toFixed(1)}s`
              ) : (
                `${matchingCount} objects${hasFilters ? " match your filters" : " to discover"}`
              )}
            </span>
          </div>
          {results && (
            <div className="query-summary">
              <span>
                <Sparkles size={15} />
                {label}
              </span>
              <button type="button" onClick={reset}>
                Back to collection <X size={14} />
              </button>
            </div>
          )}
          {displayError && (
            <div className="error-message" role="alert">
              {displayError}
              <button
                type="button"
                onClick={() => {
                  setError("")
                  void health.refetch()
                  void collection.refetch()
                }}
              >
                Try again
              </button>
            </div>
          )}
          {health.data && health.data.status !== "ready" && (
            <div className="status-notice" role="status">
              {health.data.message}
            </div>
          )}
          {loading ? (
            <div className="image-grid">
              {["one", "two", "three", "four", "five", "six", "seven", "eight"].map((key) => (
                <div className="skeleton-card" key={key} />
              ))}
            </div>
          ) : images.length ? (
            <div className={`image-grid ${busy ? "searching" : ""}`}>
              {images.map((image) => (
                <ImageCard
                  key={image.image_id}
                  image={image}
                  onOpen={setDetail}
                  onSimilar={findSimilar}
                  canSearch={available}
                />
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <Search size={30} strokeWidth={1} />
              <h3>
                {results || hasFilters ? "No images found" : "The next discovery is on its way"}
              </h3>
              <p>
                {results || hasFilters
                  ? "Try another description or broaden your filters."
                  : "Images will appear here once the sample has been indexed."}
              </p>
              {results && (
                <Button variant="outline" type="button" onClick={reset}>
                  Browse the collection
                </Button>
              )}
            </div>
          )}
          {!results && collection.hasNextPage && (
            <div className="load-more">
              <Button
                type="button"
                variant="outline"
                disabled={collection.isFetchingNextPage}
                onClick={() => void collection.fetchNextPage()}
              >
                {collection.isFetchingNextPage ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <ArrowDown size={17} />
                )}{" "}
                Discover more
              </Button>
              <span>
                Showing {images.length} of {matchingCount} images
              </span>
            </div>
          )}
          {images.length > 0 && (
            <p className="collection-note">
              <Check size={13} /> Original collection images, connected through visual and semantic
              similarity.
            </p>
          )}
        </section>
      </main>
      <footer>
        <div className="footer-brand">
          Objects tell stories.
          <br />
          <em>Find the unexpected.</em>
        </div>
        <div>
          <p>Images from the Science Museum Group collection.</p>
          <p>© The Board of Trustees of the Science Museum.</p>
          <p>
            See each image for its licence and credits. Catalogue descriptions:{" "}
            <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">
              CC BY 4.0
            </a>
            .
          </p>
        </div>
        <span className="footer-tag">A COLLECTION EXPLORER EXPERIMENT</span>
      </footer>
      <ImageDetail
        image={detail}
        onClose={() => setDetail(null)}
        onSimilar={findSimilar}
        canSearch={available}
      />
    </div>
  )
}
