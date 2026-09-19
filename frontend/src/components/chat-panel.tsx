import { ArrowUp, LoaderCircle, MessageCircle, Plus, Sparkles, X } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react"
import { ApiError, type Asset, api, type Brand, post, type Run, type Status } from "./creation/api"
import { Button } from "./ui/button"

type Message = {
  id: string
  role: "user" | "assistant"
  content: string
  asset_ids: string[]
  run_id: string
  run_status: string
  error_code: string | null
}
type Conversation = { id: string; title: string; brand_version: string }
type History = Conversation & { messages: Message[]; assets: Asset[] }
type Submission = { conversation: string; content: string; key: string; subject?: string }
const terminal = new Set(["succeeded", "failed", "cancelled", "timed_out"])
const messageOf = (error: unknown) =>
  error instanceof Error ? error.message : "The request failed."

export function ChatPanel({ selection }: { selection?: { id: string; nonce: number } }) {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<Status | null>(null)
  const [brands, setBrands] = useState<Brand[]>([])
  const [brand, setBrand] = useState("")
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState("")
  const [history, setHistory] = useState<History | null>(null)
  const [draft, setDraft] = useState("")
  const [accessKey, setAccessKey] = useState("")
  const [subject, setSubject] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [connectionError, setConnectionError] = useState("")
  const [retry, setRetry] = useState<Submission | null>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const sending = useRef(false)
  const mounted = useRef(true)
  const messages = history?.messages ?? []
  const pending = messages.find((message) => !terminal.has(message.run_status))
  const locked = busy || Boolean(pending) || Boolean(retry)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const refresh = useCallback(async () => {
    const current = await api<Status>("/status")
    if (!mounted.current) return
    setStatus(current)
    if (current.authenticated) {
      const [profiles, chats] = await Promise.all([
        api<Brand[]>("/brands"),
        api<Conversation[]>("/conversations"),
      ])
      if (!mounted.current) return
      setBrands(profiles)
      setBrand((id) => id || profiles[0]?.id || "")
      setConversations(chats)
    }
  }, [])

  useEffect(() => {
    if (open) void refresh().catch((e) => setError(messageOf(e)))
  }, [open, refresh])

  useEffect(() => {
    if (!selection) return
    setOpen(true)
    setDraft(`Use image ID ${selection.id} to create an image in my brand style.`)
    setSubject(undefined)
    input.current?.focus()
  }, [selection])

  // Poll durable history, including after terminal status while the worker publishes its reply.
  useEffect(() => {
    if (!conversation || !open || !status?.authenticated) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await api<History>(`/conversations/${conversation}`, {
          signal: controller.signal,
        })
        if (controller.signal.aborted) return
        setHistory(result)
        setConnectionError("")
      } catch (e) {
        if (!controller.signal.aborted) {
          setConnectionError(messageOf(e))
          if (e instanceof ApiError && e.status === 401) void refresh().catch(() => {})
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000)
    }
    void poll()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [conversation, open, status?.authenticated, refresh])

  useEffect(() => {
    if (open && (messages.length || pending?.run_status))
      transcript.current?.scrollTo({ top: transcript.current.scrollHeight })
  }, [open, messages.length, pending?.run_status])

  useEffect(() => {
    if (!open) return
    input.current?.focus({ preventScroll: true })
    function handleEscape(event: KeyboardEvent) {
      if (
        event.key === "Escape" &&
        !event.defaultPrevented &&
        panel.current?.contains(document.activeElement)
      ) {
        setOpen(false)
        toggle.current?.focus({ preventScroll: true })
      }
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [open])

  async function login(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError("")
    try {
      await post("/session", { access_token: accessKey })
      setAccessKey("")
      await refresh()
    } catch (e) {
      setError(messageOf(e))
    } finally {
      setBusy(false)
    }
  }

  async function send(saved?: Submission) {
    if (
      sending.current ||
      (!saved &&
        (!draft.trim() ||
          locked ||
          !brand ||
          !status?.authenticated ||
          Boolean(conversation && !history)))
    )
      return
    sending.current = true
    setBusy(true)
    setError("")
    let submission = saved
    try {
      if (!submission) {
        let id = conversation
        if (!id) {
          const created = await post<Conversation>("/conversations", {
            brand_version: brand,
            title: draft.trim().slice(0, 120),
          })
          id = created.id
          setConversations((items) => [created, ...items])
          setConversation(id)
        }
        submission = { conversation: id, content: draft.trim(), key: crypto.randomUUID(), subject }
      }
      // Retain the same body/key after a lost response: retry must not create another paid turn.
      setRetry(submission)
      await post<{ run: Run }>(
        `/conversations/${submission.conversation}/messages`,
        {
          content: submission.content,
          ...(submission.subject ? { subject_asset_id: submission.subject } : {}),
        },
        { "Idempotency-Key": submission.key },
      )
      if (!mounted.current) return
      setRetry(null)
      setDraft("")
      setSubject(undefined)
      const result = await api<History>(`/conversations/${submission.conversation}`)
      if (mounted.current) setHistory(result)
    } catch (e) {
      if (mounted.current) {
        setError(messageOf(e))
        if (e instanceof ApiError && [400, 403, 404, 409, 422].includes(e.status)) setRetry(null)
        if (e instanceof ApiError && e.status === 401) void refresh().catch(() => {})
      }
    } finally {
      sending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  function choose(id: string) {
    setConversation(id)
    setHistory(null)
    setSubject(undefined)
    setError("")
    setConnectionError("")
    if (id) setBrand(conversations.find((item) => item.id === id)?.brand_version || brand)
  }

  async function cancel() {
    if (!pending) return
    setBusy(true)
    try {
      await post(`/runs/${pending.run_id}/cancel`, {})
      setHistory(await api<History>(`/conversations/${conversation}`))
    } catch (e) {
      setError(messageOf(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        ref={toggle}
        type="button"
        className="chat-toggle"
        aria-label={open ? "Hide collection chat" : "Open collection chat"}
        aria-expanded={open}
        aria-controls="collection-chat"
        onClick={() => setOpen(!open)}
      >
        <MessageCircle size={19} aria-hidden="true" />
        <span>Chat</span>
      </button>
      <aside
        ref={panel}
        id="collection-chat"
        className="chat-panel"
        aria-labelledby="chat-title"
        hidden={!open}
      >
        <div className="chat-header">
          <div className="chat-avatar">
            <Sparkles size={20} aria-hidden="true" />
          </div>
          <div className="chat-title">
            <h2 id="chat-title">Collection companion</h2>
            <p>Create with your brand</p>
          </div>
          <button
            type="button"
            className="chat-icon-button"
            aria-label="Close collection chat"
            onClick={() => {
              setOpen(false)
              toggle.current?.focus()
            }}
          >
            <X size={20} />
          </button>
        </div>
        <div className="chat-toolbar">
          <a href="/create">Manage brand</a>
          <button
            type="button"
            className="chat-icon-button"
            aria-label="New chat"
            disabled={locked}
            onClick={() => {
              choose("")
              setDraft("")
              input.current?.focus()
            }}
          >
            <Plus size={18} />
          </button>
        </div>
        <div className="chat-settings">
          {!status && <p role="status">Connecting to your workspace…</p>}
          {status && !status.enabled && <p role="status">{status.message}</p>}
          {status?.enabled && !status.authenticated && (
            <form onSubmit={login}>
              <label htmlFor="chat-key">Workspace access key</label>
              <input
                id="chat-key"
                type="password"
                value={accessKey}
                onChange={(e) => setAccessKey(e.target.value)}
                autoComplete="off"
                required
              />
              <Button type="submit" disabled={busy}>
                Sign in
              </Button>
            </form>
          )}
          {status?.authenticated && (
            <>
              <label htmlFor="chat-brand">Brand</label>
              <select
                id="chat-brand"
                value={brand}
                disabled={Boolean(conversation) || locked}
                onChange={(e) => setBrand(e.target.value)}
              >
                <option value="">Select a brand</option>
                {brands.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · v{item.version}
                  </option>
                ))}
              </select>
              {!brands.length && (
                <p>
                  <a href="/create">Create your brand in Image Studio</a>, then return to chat.
                </p>
              )}
              <label htmlFor="chat-history">Conversation</label>
              <select
                id="chat-history"
                value={conversation}
                disabled={locked}
                onChange={(e) => choose(e.target.value)}
              >
                <option value="">New conversation</option>
                {conversations.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
              {!status.ready && <p role="status">{status.message}</p>}
            </>
          )}
        </div>
        <div className="chat-transcript" ref={transcript}>
          {!messages.length && (
            <div className="chat-welcome">
              <h3>
                Make it
                <br />
                your own.
              </h3>
              <p>
                Select a brand and use a collection image in chat. Describe the style or colors you
                want.
              </p>
            </div>
          )}
          <div
            role="log"
            aria-label="Chat messages"
            aria-live="polite"
            aria-relevant="additions text"
          >
            {messages.map((message) => (
              <div key={message.id} className={`chat-message chat-message-${message.role}`}>
                <span className="chat-message-author">
                  {message.role === "user" ? "You" : "Companion"}
                </span>
                <p>
                  {message.role === "assistant" &&
                  message.run_status === "failed" &&
                  message.asset_ids.length > 0
                    ? "Your images were generated and saved, but review could not finish. Please inspect them before use. You do not need to generate them again."
                    : message.content}
                </p>
                {message.role === "assistant" &&
                  message.run_status === "failed" &&
                  message.asset_ids.length > 0 && (
                    <p className="chat-turn-error">Needs review · Not approved</p>
                  )}
                {message.asset_ids.map((id) => {
                  const asset = history?.assets.find((item) => item.id === id)
                  return (
                    asset && (
                      <div key={id} className="chat-result">
                        <img src={asset.url} alt="Chat result" />
                        <a href={asset.url} download={`brand-image-${id}.png`}>
                          Download image
                        </a>
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => {
                            setSubject(id)
                            setDraft("Edit this image: ")
                            input.current?.focus()
                          }}
                        >
                          Edit this image
                        </button>
                      </div>
                    )
                  )
                })}
                {message.role === "user" &&
                  terminal.has(message.run_status) &&
                  message.run_status !== "succeeded" && (
                    <p className="chat-turn-error">
                      Task {message.run_status.replaceAll("_", " ")}
                      {message.error_code ? ` (${message.error_code})` : ""}. You can send another
                      message.
                    </p>
                  )}
              </div>
            ))}
          </div>
          {pending && (
            <div className="chat-pending" role="status">
              <LoaderCircle className="spin" size={14} />
              {pending.run_status.replaceAll("_", " ")}
              <button type="button" disabled={busy} onClick={() => void cancel()}>
                Cancel task
              </button>
            </div>
          )}
          {connectionError && (
            <p role="status">Connection interrupted. Retrying… {connectionError}</p>
          )}
          {error && (
            <div className="chat-error" role="alert">
              <p>{error}</p>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  retry
                    ? void send(retry)
                    : void refresh()
                        .then(() => setError(""))
                        .catch((e) => setError(messageOf(e)))
                }
              >
                Try again
              </Button>
            </div>
          )}
        </div>
        <form
          className="chat-composer"
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          {subject && (
            <p>
              Editing selected result{" "}
              <button type="button" disabled={locked} onClick={() => setSubject(undefined)}>
                Clear
              </button>
            </p>
          )}
          <label className="sr-only" htmlFor="chat-message">
            Message the collection companion
          </label>
          <div className="chat-input-wrap">
            <textarea
              id="chat-message"
              ref={input}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  e.currentTarget.form?.requestSubmit()
                }
              }}
              placeholder="Use image ID … in my brand style"
              rows={3}
              maxLength={6000}
              aria-describedby="chat-input-help"
              disabled={busy || Boolean(retry)}
            />
            <Button
              className="chat-send"
              type="submit"
              aria-label="Send message"
              disabled={
                !draft.trim() ||
                locked ||
                !status?.authenticated ||
                !brand ||
                Boolean(conversation && !history)
              }
            >
              <ArrowUp size={19} />
            </Button>
          </div>
          <p id="chat-input-help">Enter to send · Shift + Enter for a new line</p>
        </form>
      </aside>
    </>
  )
}
