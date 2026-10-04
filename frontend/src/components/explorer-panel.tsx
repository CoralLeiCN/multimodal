import { ArrowUp, LoaderCircle, MessageCircle, Plus, X } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react"
import {
  explorerCancel,
  explorerConversations,
  explorerCreate,
  explorerHistory,
  explorerLogout,
  explorerStatus,
  explorerSubmit,
  explorerUpload,
} from "../client/api"
import type {
  ExplorerConversation as Conversation,
  ExplorerHistory as History,
  Message,
  ExplorerStatus as Status,
} from "../client/generated"
import { Licence } from "./licence"
import { Button } from "./ui/button"
import "./explorer.css"

type Submission = Message & { conversation: string; key: string }
const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out"])
const toolLabels: Record<string, string> = {
  search_text: "Searching descriptions…",
  search_image: "Searching by image…",
  find_similar: "Finding similar images…",
  get_filter_options: "Checking collection filters…",
  get_image_details: "Inspecting collection evidence…",
  lookup_record: "Looking up the collection ID…",
}
class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}
async function readExplorer<T>(
  request: Promise<{ data?: T; error?: unknown; response?: Response }>,
): Promise<T> {
  const { data, error, response } = await request
  if (!response) {
    throw error instanceof Error ? error : new Error("We couldn't reach the collection.")
  }
  if (!response.ok) {
    const body = error as { message?: unknown; detail?: unknown } | null
    const message = body?.message || body?.detail
    throw new ApiError(
      response.status,
      typeof message === "string" ? message : "Check your message, image and request settings.",
    )
  }
  if (data === undefined) throw new Error("The server returned an empty response.")
  return data
}
const messageOf = (error: unknown) =>
  error instanceof Error ? error.message : "The request failed."

export function ExplorerPanel({ selection }: { selection?: { text: string; nonce: number } }) {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<Status>()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState("")
  const [history, setHistory] = useState<History>()
  const [draft, setDraft] = useState("")
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [progress, setProgress] = useState("")
  const [retry, setRetry] = useState<Submission>()
  const input = useRef<HTMLTextAreaElement>(null)
  const uploadInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!file && uploadInput.current) uploadInput.current.value = ""
  }, [file])
  const toggle = useRef<HTMLButtonElement>(null)
  const sendLock = useRef(false)
  const historyEpoch = useRef(0)
  const conversationRef = useRef(conversation)
  conversationRef.current = conversation
  const pending = history?.runs.find((r) => !terminal.has(r.status))
  const locked = busy || Boolean(pending) || Boolean(retry)

  const refresh = useCallback(async () => {
    const current = await readExplorer(explorerStatus())
    setStatus(current)
    if (current.authenticated) setConversations(await readExplorer(explorerConversations()))
  }, [])
  useEffect(() => {
    if (open) void refresh().catch((e) => setError(messageOf(e)))
  }, [open, refresh])
  useEffect(() => {
    if (selection) {
      setOpen(true)
      setDraft(selection.text)
      input.current?.focus()
    }
  }, [selection])
  useEffect(() => {
    if (open && status?.authenticated && !locked) input.current?.focus()
  }, [open, status?.authenticated, locked])
  useEffect(() => {
    if (!conversation || !status?.authenticated) {
      setHistory(undefined)
      return
    }
    let active = true
    const epoch = historyEpoch.current
    void readExplorer(explorerHistory({ path: { conversation_id: conversation } }))
      .then((data) => {
        if (active && historyEpoch.current === epoch) setHistory(data)
      })
      .catch((e) => {
        if (active) setError(messageOf(e))
      })
    return () => {
      active = false
    }
  }, [conversation, status?.authenticated])
  const activeRun = pending?.id
  useEffect(() => {
    if (!activeRun || !conversation || !open) return
    let active = true
    const source = new EventSource(`/api/v1/explorer/runs/${activeRun}/events`)
    const load = () =>
      readExplorer(explorerHistory({ path: { conversation_id: conversation } }))
        .then((data) => {
          if (active) setHistory(data)
        })
        .catch((e) => {
          if (active) setError(messageOf(e))
        })
    source.addEventListener("tool", (event) => {
      const data = JSON.parse((event as MessageEvent).data)
      setProgress(toolLabels[data.name] || "Exploring the collection…")
    })
    source.addEventListener("status", (event) => {
      const data = JSON.parse((event as MessageEvent).data)
      if (terminal.has(data.status)) {
        source.close()
        setProgress("")
        void load()
      }
    })
    source.onerror = () => {
      if (active) setProgress("Reconnecting to the conversation…")
    }
    // Polling also reconciles a terminal run if the final SSE connection was lost.
    const timer = window.setInterval(() => void load(), 3000)
    return () => {
      active = false
      source.close()
      window.clearInterval(timer)
    }
  }, [activeRun, conversation, open])

  async function send(event: FormEvent, previous = retry) {
    event.preventDefault()
    if (sendLock.current || (!previous && (pending || !draft.trim()))) return
    sendLock.current = true
    setBusy(true)
    setError("")
    let submission = previous
    let accepted = false
    try {
      if (!submission) {
        let id = conversation
        if (!id) {
          const created = await readExplorer(
            explorerCreate({ body: { title: draft.slice(0, 80) } }),
          )
          id = created.id
          setConversation(id)
          setConversations((current) => [created, ...current])
        }
        let uploadId: string | undefined
        if (file) {
          uploadId = (await readExplorer(explorerUpload({ body: { image: file } }))).id
        }
        submission = {
          conversation: id,
          content: draft.trim(),
          upload_id: uploadId,
          key: crypto.randomUUID(),
        }
      }
      await readExplorer(
        explorerSubmit({
          path: { conversation_id: submission.conversation },
          body: { content: submission.content, upload_id: submission.upload_id },
          headers: { "Idempotency-Key": submission.key },
        }),
      )
      accepted = true
      historyEpoch.current += 1
      setRetry(undefined)
      setDraft("")
      setFile(undefined)
      setHistory(
        await readExplorer(explorerHistory({ path: { conversation_id: submission.conversation } })),
      )
    } catch (failure) {
      setError(messageOf(failure))
      // A rejected POST can be edited or abandoned. An uncertain outcome (including
      // a failed history refresh after acceptance) must retain its idempotency key.
      const rejected =
        !accepted && failure instanceof ApiError && failure.status >= 400 && failure.status < 500
      setRetry(rejected ? undefined : submission)
    } finally {
      sendLock.current = false
      setBusy(false)
    }
  }
  function close() {
    setOpen(false)
    toggle.current?.focus()
  }

  return (
    <>
      <button
        type="button"
        className="explorer-toggle"
        ref={toggle}
        onClick={() => setOpen((value) => !value)}
        aria-label="Open collection companion"
        aria-expanded={open}
      >
        <MessageCircle size={20} /> Explore with an agent
      </button>
      {open && (
        <aside
          className="explorer-panel"
          aria-label="Collection companion"
          onKeyDown={(event) => {
            if (event.key === "Escape") close()
          }}
        >
          <header>
            <div>
              <h2>Collection companion</h2>
              <p>Ask, compare and explore.</p>
            </div>
            <Button variant="ghost" onClick={close} aria-label="Close collection companion">
              <X size={20} />
            </Button>
          </header>
          {error && (
            <p role="alert" className="explorer-error">
              {error}
            </p>
          )}
          {!status ? (
            <p role="status">Loading…</p>
          ) : !status.authenticated ? (
            <div className="explorer-welcome">
              <p>
                Explore the collection in a conversation. Your chats are private to your account.
              </p>
              {status.auth_ready ? (
                <a className="explorer-login" href="/api/v1/explorer/auth/login">
                  Sign in with Hugging Face
                </a>
              ) : (
                <p>Sign-in is not configured yet.</p>
              )}
            </div>
          ) : (
            <>
              <div className="explorer-controls">
                <label>
                  Conversation
                  <select
                    aria-label="Collection conversation"
                    value={conversation}
                    disabled={busy || Boolean(retry)}
                    onChange={(e) => {
                      setConversation(e.target.value)
                      setHistory(undefined)
                      setProgress("")
                      setError("")
                    }}
                  >
                    <option value="">New conversation</option>
                    {conversations.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.title}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  variant="ghost"
                  disabled={busy || Boolean(retry)}
                  onClick={() => {
                    setConversation("")
                    setHistory(undefined)
                    setDraft("")
                    setFile(undefined)
                    setError("")
                  }}
                  aria-label="New collection conversation"
                >
                  <Plus size={18} />
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() =>
                    void readExplorer(explorerLogout())
                      .then(() => {
                        setStatus({ ...status, authenticated: false })
                        setConversation("")
                        setHistory(undefined)
                        setConversations([])
                        setDraft("")
                        setFile(undefined)
                        setRetry(undefined)
                      })
                      .catch((e) => setError(messageOf(e)))
                  }
                >
                  Sign out
                </Button>
              </div>
              <div role="log" aria-live="polite" className="explorer-transcript">
                {!history?.runs.length && (
                  <p>
                    Try “Find British cameras made before 1950” or select an image from the
                    collection.
                  </p>
                )}
                {history?.runs.map((run) => (
                  <section key={run.id} className="explorer-turn">
                    <p className="explorer-user">{run.content}</p>
                    {run.answer && <p className="explorer-answer">{run.answer}</p>}
                    {!!run.results.length && (
                      <div className="explorer-results">
                        {run.results.map((image) => (
                          <article key={image.image_id}>
                            <img src={image.image_url} alt={image.title} loading="lazy" />
                            <h3>{image.title}</h3>
                            {image.associations.map((a) => (
                              <p key={`${a.record_uid}-${a.image_uid}`}>
                                <a href={a.source_url} target="_blank" rel="noreferrer">
                                  {a.record_uid}
                                </a>{" "}
                                · <Licence value={a.licence} />
                                <br />
                                {a.credit}
                                {a.copyright && (
                                  <>
                                    <br />
                                    {a.copyright}
                                  </>
                                )}
                              </p>
                            ))}
                            <Button
                              variant="ghost"
                              disabled={locked}
                              onClick={() => {
                                setDraft(`Tell me more about image ${image.image_id}`)
                                input.current?.focus()
                              }}
                            >
                              Explore this image
                            </Button>
                          </article>
                        ))}
                      </div>
                    )}
                    {run.error && (
                      <p role="status">
                        {run.status === "cancelled"
                          ? "Exploration cancelled."
                          : `Exploration stopped: ${run.error}. You can start another turn.`}
                      </p>
                    )}
                  </section>
                ))}
              </div>
              {pending && (
                <div className="explorer-progress">
                  <span role="status">
                    <LoaderCircle size={16} className="spin" />{" "}
                    {progress || "Exploring the collection…"}
                  </span>
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void readExplorer(explorerCancel({ path: { run_id: pending.id } }))
                        .then(() =>
                          readExplorer(
                            explorerHistory({ path: { conversation_id: conversation } }),
                          ),
                        )
                        .then((data) => {
                          if (conversationRef.current === data.id) setHistory(data)
                        })
                        .catch((e) => setError(messageOf(e)))
                    }
                  >
                    Stop exploration
                  </Button>
                </div>
              )}
              {!status.ready && (
                <p role="status">
                  The collection companion is not configured yet. Ordinary search is available.
                </p>
              )}
              <form onSubmit={(event) => void send(event)}>
                <textarea
                  ref={input}
                  aria-label="Ask about the collection"
                  value={draft}
                  maxLength={6000}
                  disabled={locked || !status.ready}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="What would you like to discover?"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault()
                      void send(event)
                    }
                  }}
                />
                <div className="explorer-compose">
                  <label className="explorer-upload">
                    {file?.name || "Attach an image"}
                    <input
                      ref={uploadInput}
                      aria-label="Attach image to conversation"
                      type="file"
                      accept="image/jpeg,image/png"
                      disabled={locked || !status.ready}
                      onChange={(e) => setFile(e.target.files?.[0])}
                    />
                  </label>
                  <Button
                    type="submit"
                    aria-label="Send exploration message"
                    disabled={locked || !draft.trim() || !status.ready}
                  >
                    <ArrowUp size={18} />
                  </Button>
                </div>
                {retry && (
                  <Button
                    type="button"
                    disabled={busy}
                    onClick={(event) => void send(event, retry)}
                  >
                    Retry message
                  </Button>
                )}
              </form>
            </>
          )}
        </aside>
      )}
    </>
  )
}
