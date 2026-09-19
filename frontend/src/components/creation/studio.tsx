import { Link } from "@tanstack/react-router"
import { ArrowLeft, Download, LoaderCircle, Sparkles, X } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react"
import {
  type Asset,
  api,
  type Brand,
  post,
  type Run,
  type RunEvent,
  type Status,
  upload,
} from "./api"

const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out"])
const emptyProfile = { name: "", description: "", colors: "", preserve: "", avoid: "" }

export function CreationStudio() {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [accessKey, setAccessKey] = useState("")
  const [brands, setBrands] = useState<Brand[]>([])
  const [brandId, setBrandId] = useState("")
  const [profile, setProfile] = useState(emptyProfile)
  const [editingBrand, setEditingBrand] = useState<string | null>(null)
  const [references, setReferences] = useState<Asset[]>([])
  const [subject, setSubject] = useState<Asset | null>(null)
  const [prompt, setPrompt] = useState("")
  const [ratio, setRatio] = useState("1:1")
  const [count, setCount] = useState(2)
  const [subjectStrength, setSubjectStrength] = useState("high")
  const [styleStrength, setStyleStrength] = useState("medium")
  const [run, setRun] = useState<Run | null>(null)
  const [history, setHistory] = useState<Run[]>([])
  const [events, setEvents] = useState<RunEvent[]>([])
  const [answer, setAnswer] = useState("")
  const [edit, setEdit] = useState<{ runId: string; asset: Asset } | null>(null)
  const [connectionNotice, setConnectionNotice] = useState("")
  const submission = useRef<{ body: string; key: string } | null>(null)

  const refresh = useCallback(async () => {
    const current = await api<Status>("/status")
    setStatus(current)
    if (current.authenticated) {
      const [profiles, runs] = await Promise.all([api<Brand[]>("/brands"), api<Run[]>("/runs")])
      setBrands(profiles)
      setHistory(runs)
      setBrandId((id) => id || profiles[0]?.id || "")
    }
  }, [])
  useEffect(() => {
    refresh().catch((e: Error) => setError(e.message))
  }, [refresh])

  const runId = run?.id
  const runStatus = run?.status
  useEffect(() => {
    if (!runId || !runStatus) return
    setEvents([])
    setConnectionNotice("")
    const source = new EventSource(`/api/v1/agent/runs/${runId}/events`)
    let active = true
    const update = async () => {
      try {
        const result = await api<Run>(`/runs/${runId}`)
        if (active) setRun(result)
      } catch (e) {
        if (active) setConnectionNotice((e as Error).message)
      }
    }
    source.onmessage = (event) => {
      const item: RunEvent = JSON.parse(event.data)
      setEvents((items) =>
        items.some((i) => i.sequence === item.sequence) ? items : [...items, item],
      )
      setConnectionNotice("")
      void update()
    }
    source.addEventListener("done", () => {
      source.close()
      void update()
      refresh().catch((e: Error) => setError(e.message))
    })
    source.onerror = () =>
      setConnectionNotice("Reconnecting to progress. Your run continues in the cloud.")
    const timer = setInterval(update, 5000)
    return () => {
      active = false
      clearInterval(timer)
      source.close()
    }
  }, [runId, runStatus, refresh])

  async function action(work: () => Promise<void>) {
    setBusy(true)
    setError("")
    try {
      await work()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  async function signIn(event: FormEvent) {
    event.preventDefault()
    await action(async () => {
      await post("/session", { access_token: accessKey })
      setAccessKey("")
      await refresh()
    })
  }
  async function saveBrand(event: FormEvent) {
    event.preventDefault()
    await action(async () => {
      const brand = await post<Brand>(
        editingBrand ? `/brands/${editingBrand}/versions` : "/brands",
        {
          ...profile,
          reference_asset_ids: references.map((r) => r.id),
        },
      )
      await refresh()
      setBrandId(brand.id)
      setEditingBrand(brand.brand_id)
    })
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    await action(async () => {
      const payload = {
        brand_version: brandId,
        prompt,
        subject_asset_ids: subject && !edit ? [subject.id] : [],
        aspect_ratio: ratio,
        candidate_count: count,
        subject_strength: subjectStrength,
        style_strength: styleStrength,
        parent_run_id: edit?.runId || null,
        selected_asset_id: edit?.asset.id || null,
      }
      const body = JSON.stringify(payload)
      if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() }
      const result = await post<Run>("/runs", payload, {
        "Idempotency-Key": submission.current.key,
      })
      setRun(result)
      submission.current = null
      setEdit(null)
      await refresh()
    })
  }
  function loadProfile() {
    const brand = brands.find((b) => b.id === brandId)
    if (!brand) return
    setProfile({
      name: brand.name,
      description: brand.description,
      colors: brand.colors,
      preserve: brand.preserve,
      avoid: brand.avoid,
    })
    setEditingBrand(brand.brand_id)
    setReferences(
      brand.reference_asset_ids.map((id) => ({
        id,
        url: `/api/v1/agent/assets/${id}/file`,
        width: 0,
        height: 0,
      })),
    )
  }

  return (
    <main className="app-shell studio-shell">
      <header className="site-header">
        <Link to="/" className="header-link">
          <ArrowLeft size={16} /> Collection Explorer
        </Link>
        <span className="eyebrow">BRAND IMAGE STUDIO</span>
        {status?.authenticated && (
          <button
            className="button button-ghost"
            type="button"
            onClick={() =>
              action(async () => {
                await api("/session", { method: "DELETE" })
                setRun(null)
                setBrands([])
                setHistory([])
                await refresh()
              })
            }
          >
            Sign out
          </button>
        )}
      </header>
      <section className="hero studio-hero">
        <div className="eyebrow">
          <span className="tiny-dot" /> FROM REFERENCE TO ORIGINAL
        </div>
        <div className="hero-heading">
          <h1>
            Make it
            <br />
            <em>your own.</em>
          </h1>
          <p>
            Your subject. Your brand’s visual language.
            <br />
            Create, compare, and refine with an image agent.
          </p>
        </div>
      </section>
      {error && (
        <div className="error-message" role="alert">
          {error}
          <button type="button" onClick={() => setError("")}>
            Dismiss
          </button>
        </div>
      )}
      {!status && !error && <p role="status">Loading your studio…</p>}
      {status && !status.enabled && (
        <div className="status-notice">
          The studio is ready for setup. Image creation is not enabled on this server yet.
        </div>
      )}
      {status?.enabled && !status.authenticated && (
        <form className="studio-panel studio-login" onSubmit={signIn}>
          <h2>Open your workspace</h2>
          <p>Use the access key provided by your workspace administrator.</p>
          <label>
            Workspace access key
            <input
              type="password"
              autoComplete="current-password"
              required
              value={accessKey}
              onChange={(e) => setAccessKey(e.target.value)}
            />
          </label>
          <button type="submit" className="button button-primary" disabled={busy}>
            Sign in
          </button>
        </form>
      )}
      {status?.authenticated && (
        <>
          {!status.ready && (
            <div className="status-notice">
              {status.message} Missing: {status.missing?.join(", ")}.
            </div>
          )}
          <div className="studio-grid">
            <aside className="studio-panel">
              <div className="eyebrow">01 / YOUR VISUAL LANGUAGE</div>
              <h2>{editingBrand ? "Refine your brand" : "Create a brand"}</h2>
              <form onSubmit={saveBrand}>
                <fieldset disabled={busy}>
                  <label>
                    Brand name
                    <input
                      required
                      maxLength={100}
                      value={profile.name}
                      onChange={(e) => setProfile({ ...profile, name: e.target.value })}
                      placeholder="e.g. Fieldwork Studio"
                    />
                  </label>
                  <label>
                    Brand description
                    <textarea
                      required
                      maxLength={6000}
                      rows={4}
                      value={profile.description}
                      onChange={(e) => setProfile({ ...profile, description: e.target.value })}
                      placeholder="Warm editorial photography, natural materials, soft daylight…"
                    />
                  </label>
                  <label>
                    Brand colors
                    <input
                      maxLength={500}
                      value={profile.colors}
                      onChange={(e) => setProfile({ ...profile, colors: e.target.value })}
                      placeholder="Moss green, ivory, charcoal"
                    />
                  </label>
                  <label>
                    Elements to preserve
                    <textarea
                      rows={2}
                      maxLength={2000}
                      value={profile.preserve}
                      onChange={(e) => setProfile({ ...profile, preserve: e.target.value })}
                    />
                  </label>
                  <label>
                    Elements to avoid
                    <textarea
                      rows={2}
                      maxLength={2000}
                      value={profile.avoid}
                      onChange={(e) => setProfile({ ...profile, avoid: e.target.value })}
                    />
                  </label>
                  <label>
                    Style references{" "}
                    <span className="studio-hint">
                      Up to 3 images · PNG, JPEG, WebP · 10 MB each
                    </span>
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      multiple
                      disabled={references.length >= 3}
                      onChange={(e) => {
                        const files = Array.from(e.target.files || [])
                        e.target.value = ""
                        void action(async () => {
                          if (files.length + references.length > 3)
                            throw new Error("Choose up to 3 style references.")
                          const uploaded = await Promise.all(
                            files.map((file) => upload(file, "reference")),
                          )
                          setReferences((items) => [...items, ...uploaded])
                        })
                      }}
                    />
                  </label>
                  <div className="studio-thumbnails">
                    {references.map((asset) => (
                      <div key={asset.id}>
                        <img src={asset.url} alt="Brand style reference" />
                        <button
                          type="button"
                          aria-label="Remove reference"
                          onClick={() => setReferences(references.filter((r) => r.id !== asset.id))}
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <button type="submit" className="button button-primary">
                    {editingBrand ? "Save new brand version" : "Save brand"}
                  </button>
                  {editingBrand && (
                    <button
                      type="button"
                      className="button button-ghost"
                      onClick={() => {
                        setEditingBrand(null)
                        setProfile(emptyProfile)
                        setReferences([])
                      }}
                    >
                      New brand
                    </button>
                  )}
                </fieldset>
              </form>
            </aside>
            <section className="studio-panel">
              <div className="eyebrow">02 / YOUR CREATIVE DIRECTION</div>
              <h2>Create something new</h2>
              <form onSubmit={submit}>
                <fieldset disabled={busy}>
                  <label>
                    Brand style
                    <select required value={brandId} onChange={(e) => setBrandId(e.target.value)}>
                      <option value="">Choose a saved brand</option>
                      {brands.map((b) => (
                        <option key={b.id} value={b.id}>
                          {b.name} · v{b.version}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="studio-text-button"
                    disabled={!brandId}
                    onClick={loadProfile}
                  >
                    Edit this brand as a new version
                  </button>
                  <label>
                    What would you like to create?
                    <textarea
                      required
                      rows={5}
                      maxLength={6000}
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      placeholder="A product photograph of a brass microscope, preserving its silhouette, in our brand’s editorial style…"
                    />
                  </label>
                  {edit ? (
                    <div className="studio-edit">
                      <img src={edit.asset.url} alt="Selected creation to edit" />
                      <span>Editing a previous result</span>
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() => setEdit(null)}
                      >
                        Clear
                      </button>
                    </div>
                  ) : (
                    <label>
                      Subject image{" "}
                      <span className="studio-hint">Optional · specifies what to preserve</span>
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        onChange={(e) => {
                          const file = e.target.files?.[0]
                          e.target.value = ""
                          if (file)
                            void action(async () => setSubject(await upload(file, "subject")))
                        }}
                      />
                    </label>
                  )}
                  {subject && !edit && (
                    <div className="studio-edit">
                      <img src={subject.url} alt="Subject reference" />
                      <button
                        type="button"
                        className="button button-ghost"
                        onClick={() => setSubject(null)}
                      >
                        Remove subject
                      </button>
                    </div>
                  )}
                  <div className="studio-options">
                    <label>
                      Aspect ratio
                      <select value={ratio} onChange={(e) => setRatio(e.target.value)}>
                        {["1:1", "4:3", "3:4", "16:9", "9:16"].map((r) => (
                          <option key={r}>{r}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Candidates
                      <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
                        <option value={1}>1 image</option>
                        <option value={2}>2 images</option>
                      </select>
                    </label>
                  </div>
                  <div className="studio-options">
                    <label>
                      Preserve subject
                      <select
                        value={subjectStrength}
                        onChange={(e) => setSubjectStrength(e.target.value)}
                      >
                        {["low", "medium", "high"].map((s) => (
                          <option key={s}>{s}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Brand influence
                      <select
                        value={styleStrength}
                        onChange={(e) => setStyleStrength(e.target.value)}
                      >
                        {["low", "medium", "high"].map((s) => (
                          <option key={s}>{s}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <p className="studio-hint">
                    The agent checks each image and may make one revision. Generation uses your
                    workspace’s paid model service.
                  </p>
                  <button
                    type="submit"
                    className="button button-primary studio-generate"
                    disabled={!status.ready || !brandId || busy}
                  >
                    {busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
                    {edit ? "Generate an edit" : "Generate images"}
                  </button>
                </fieldset>
              </form>
            </section>
          </div>
          <section className="studio-results" aria-label="Generation results">
            <div className="section-heading">
              <div>
                <div className="eyebrow">03 / EXPLORE & REFINE</div>
                <h2>Your creations</h2>
              </div>
              {history.length > 0 && (
                <label className="studio-history">
                  Recent runs
                  <select
                    aria-label="Recent runs"
                    value={run?.id || ""}
                    onChange={(e) => {
                      const selected = history.find((r) => r.id === e.target.value)
                      if (selected) setRun(selected)
                    }}
                  >
                    <option value="">Select a run</option>
                    {history.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.prompt.slice(0, 45)} · {r.status}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            {!run && (
              <div className="empty-state">
                <Sparkles size={30} />
                <h3>Your next idea starts here.</h3>
                <p>Save a brand, add your direction, and create your first image.</p>
              </div>
            )}
            {run && (
              <>
                <div className="studio-progress" aria-live="polite">
                  <strong>{run.stage.replaceAll("_", " ")}</strong>
                  <span>{events.at(-1)?.summary || "Your run has been saved."}</span>
                  {!terminal.has(run.status) && (
                    <button
                      type="button"
                      className="button button-outline"
                      disabled={busy}
                      onClick={() =>
                        action(async () => {
                          setRun(await post<Run>(`/runs/${run.id}/cancel`, {}))
                          await refresh()
                        })
                      }
                    >
                      Cancel run
                    </button>
                  )}
                </div>
                {connectionNotice && (
                  <p className="studio-hint" role="status">
                    {connectionNotice}
                  </p>
                )}
                {run.error_code && (
                  <div className="status-notice">
                    This run stopped ({run.error_code.replaceAll("_", " ")}). Any completed images
                    are preserved. Submitted generation calls are not automatically repeated.
                  </div>
                )}
                {run.question && run.status === "waiting_for_input" && (
                  <form
                    className="studio-panel"
                    onSubmit={(e) => {
                      e.preventDefault()
                      void action(async () => {
                        setRun(await post<Run>(`/runs/${run.id}/input`, { answer }))
                        setAnswer("")
                      })
                    }}
                  >
                    <label>
                      {run.question}
                      <textarea
                        required
                        value={answer}
                        maxLength={4000}
                        onChange={(e) => setAnswer(e.target.value)}
                      />
                    </label>
                    <button type="submit" className="button button-primary" disabled={busy}>
                      Continue creating
                    </button>
                  </form>
                )}
                {run.review_status === "needs_review" && (
                  <p className="status-notice">
                    These images need your review. The agent reached its revision limit.
                  </p>
                )}
                <div className="studio-candidates">
                  {run.artifacts.map((asset, i) => (
                    <article className="studio-candidate" key={asset.id}>
                      <img src={asset.url} alt={`Generated candidate ${i + 1}`} />
                      <div>
                        <h3>Candidate {i + 1}</h3>
                        <p>{asset.evaluation?.summary || "Evaluation is incomplete."}</p>
                        {asset.evaluation && (
                          <p className="studio-hint">
                            Subject {asset.evaluation.subject_score} · Brand{" "}
                            {asset.evaluation.brand_score} · Brief {asset.evaluation.request_score}
                            <br />
                            Model assessments, for guidance.
                          </p>
                        )}
                        <div className="studio-actions">
                          <a
                            className="button button-outline"
                            href={asset.url}
                            download={`creation-${asset.id}`}
                          >
                            <Download size={14} /> Download
                          </a>
                          <button
                            className="button button-primary"
                            type="button"
                            onClick={() => {
                              setEdit({ runId: run.id, asset })
                              setBrandId(run.brand_version)
                              setPrompt("")
                              window.scrollTo({ top: 280, behavior: "smooth" })
                            }}
                          >
                            Refine this image
                          </button>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
                {run.trace_id && (
                  <details className="studio-trace">
                    <summary>Run details</summary>
                    <p>
                      Run: {run.id}
                      <br />
                      Trace: {run.trace_id}
                    </p>
                    <ol>
                      {events.map((e) => (
                        <li key={e.sequence}>{e.summary}</li>
                      ))}
                    </ol>
                  </details>
                )}
              </>
            )}
          </section>
        </>
      )}
      <footer>
        <span className="footer-brand">
          Collection Explorer <em>/ Studio</em>
        </span>
        <p>Brand reference generation works independently of collection search.</p>
      </footer>
    </main>
  )
}
