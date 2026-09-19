import { Link } from "@tanstack/react-router"
import { ArrowLeft, LoaderCircle } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { api, type Brand, post, type Status } from "./api"

const emptyProfile = {
  name: "",
  description: "",
  colors: "",
  personality: "",
  typography: "",
  illustration_style: "",
}
const fields = [
  { key: "name", label: "Brand name", placeholder: "e.g. Fieldwork Studio", max: 100 },
  {
    key: "description",
    label: "Brand description",
    placeholder: "What your brand stands for, who it serves, and its visual direction",
    max: 6000,
  },
  {
    key: "colors",
    label: "Brand colors palette",
    placeholder: "Moss green #65744D, ivory #FAF8F0, charcoal #292B27",
    max: 500,
  },
  {
    key: "personality",
    label: "Personality",
    placeholder: "premium, calm, technical, optimistic",
    max: 1000,
  },
  {
    key: "typography",
    label: "Typography",
    placeholder: "Clean sans-serif headings, generous spacing, understated text",
    max: 1000,
  },
  {
    key: "illustration_style",
    label: "Illustration style",
    placeholder: "Minimal geometric shapes, fine outlines, subtle paper texture",
    max: 2000,
  },
] as const

export function CreationStudio() {
  const [status, setStatus] = useState<Status | null>(null)
  const [brands, setBrands] = useState<Brand[]>([])
  const [profile, setProfile] = useState(emptyProfile)
  const [selected, setSelected] = useState("")
  const [editing, setEditing] = useState<string | null>(null)
  const [accessKey, setAccessKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [saved, setSaved] = useState(false)
  const refresh = useCallback(async () => {
    const current = await api<Status>("/status")
    setStatus(current)
    if (current.authenticated) setBrands(await api<Brand[]>("/brands"))
  }, [])
  useEffect(() => {
    void refresh().catch((e: Error) => setError(e.message))
  }, [refresh])

  async function action(work: () => Promise<void>) {
    setBusy(true)
    setError("")
    setSaved(false)
    try {
      await work()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  function selectBrand(id: string) {
    setSelected(id)
    setSaved(false)
    const brand = brands.find((item) => item.id === id)
    setEditing(brand?.brand_id ?? null)
    setProfile(
      brand
        ? {
            name: brand.name,
            description: brand.description,
            colors: brand.colors,
            personality: brand.personality || "",
            typography: brand.typography || "",
            illustration_style: brand.illustration_style || "",
          }
        : emptyProfile,
    )
  }
  function save(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const brand = await post<Brand>(editing ? `/brands/${editing}/versions` : "/brands", profile)
      setEditing(brand.brand_id)
      setSelected(brand.id)
      await refresh()
      setSaved(true)
    })
  }

  return (
    <main className="app-shell studio-shell brand-page">
      <header className="site-header">
        <Link to="/" className="header-link">
          <ArrowLeft size={16} /> Collection Explorer
        </Link>
        <span className="eyebrow">YOUR BRAND</span>
        {status?.authenticated && (
          <button
            className="button button-ghost"
            type="button"
            disabled={busy}
            onClick={() =>
              void action(async () => {
                await api("/session", { method: "DELETE" })
                setBrands([])
                selectBrand("")
                await refresh()
              })
            }
          >
            Sign out
          </button>
        )}
      </header>
      <section className="brand-heading">
        <h1>Create a brand</h1>
        <p>
          Define your visual language. Use it in chat to turn collection images into designs for
          your brand.
        </p>
      </section>
      {error && (
        <div className="error-message" role="alert">
          {error}
          <button
            type="button"
            onClick={() => {
              setError("")
              void refresh().catch((e: Error) => setError(e.message))
            }}
          >
            Try again
          </button>
        </div>
      )}
      {!status && !error && <p role="status">Loading your workspace…</p>}
      {status && !status.enabled && (
        <p role="status">Brand creation is not enabled on this server yet.</p>
      )}
      {status?.enabled && !status.authenticated && (
        <form
          className="studio-panel studio-login"
          onSubmit={(e) => {
            e.preventDefault()
            void action(async () => {
              await post("/session", { access_token: accessKey })
              setAccessKey("")
              await refresh()
            })
          }}
        >
          <h2>Open your workspace</h2>
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
        <section className="studio-panel brand-form">
          {brands.length > 0 && (
            <label>
              Saved brands
              <select
                value={selected}
                disabled={busy}
                onChange={(e) => selectBrand(e.target.value)}
              >
                <option value="">Create a new brand</option>
                {brands.map((brand) => (
                  <option key={brand.id} value={brand.id}>
                    {brand.name} · v{brand.version}
                  </option>
                ))}
              </select>
            </label>
          )}
          <form onSubmit={save}>
            <fieldset disabled={busy}>
              {fields.map((field) => (
                <div className="brand-field" key={field.key}>
                  <label htmlFor={`brand-${field.key}`}>{field.label}</label>
                  {field.key === "description" || field.key === "illustration_style" ? (
                    <textarea
                      id={`brand-${field.key}`}
                      rows={field.key === "description" ? 4 : 3}
                      required={field.key === "description"}
                      maxLength={field.max}
                      placeholder={field.placeholder}
                      value={profile[field.key]}
                      onChange={(e) => {
                        setProfile({ ...profile, [field.key]: e.target.value })
                        setSaved(false)
                      }}
                    />
                  ) : (
                    <input
                      id={`brand-${field.key}`}
                      required={field.key === "name"}
                      maxLength={field.max}
                      placeholder={field.placeholder}
                      value={profile[field.key]}
                      onChange={(e) => {
                        setProfile({ ...profile, [field.key]: e.target.value })
                        setSaved(false)
                      }}
                    />
                  )}
                </div>
              ))}
              <button className="button button-primary" type="submit">
                {busy && <LoaderCircle className="spin" size={16} />}
                {editing ? "Save brand changes" : "Save brand"}
              </button>
            </fieldset>
          </form>
          {saved && (
            <p role="status">
              Brand saved. <Link to="/">Open the collection and start a chat</Link>.
            </p>
          )}
        </section>
      )}
    </main>
  )
}
