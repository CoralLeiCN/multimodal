import { ArrowRight, ArrowUp, LoaderCircle, MessageCircle, Plus, Sparkles, X } from "lucide-react"
import { type FormEvent, useEffect, useRef, useState } from "react"
import { Button } from "./ui/button"

export type ChatMessage = {
  id: string
  role: "user" | "assistant"
  content: string
}

// Supply this handler when connecting the agent. History includes the latest user message.
export type ChatReplyHandler = (
  messages: readonly ChatMessage[],
  signal: AbortSignal,
) => Promise<string>

const prompts = [
  "Help me discover something unexpected",
  "Tell me about early computers",
  "How did people explore the world?",
]

export function ChatPanel({ onSend }: { onSend?: ChatReplyHandler }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const toggle = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const request = useRef<AbortController | null>(null)
  const preview = !onSend

  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    if (open && (messages.length || busy || error)) {
      transcript.current?.scrollTo({ top: transcript.current.scrollHeight })
    }
  }, [open, messages.length, busy, error])

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

  function close() {
    setOpen(false)
    toggle.current?.focus({ preventScroll: true })
  }

  async function reply(history: ChatMessage[]) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError("")
    try {
      const content = onSend
        ? await onSend(history, controller.signal)
        : "This is a preview conversation. Once the collection agent is connected, you’ll be able to explore objects and their stories here. For now, try the collection search to follow your curiosity."
      if (!content.trim()) throw new Error("Empty reply")
      if (!controller.signal.aborted) {
        setMessages([...history, { id: crypto.randomUUID(), role: "assistant", content }])
      }
    } catch {
      if (!controller.signal.aborted) {
        setError("Couldn’t get a reply. Please try again.")
      }
    } finally {
      if (!controller.signal.aborted) {
        request.current = null
        setBusy(false)
      }
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || busy || error) return
    const history: ChatMessage[] = [...messages, { id: crypto.randomUUID(), role: "user", content }]
    setMessages(history)
    setDraft("")
    void reply(history)
    input.current?.focus()
  }

  function newChat() {
    request.current?.abort()
    request.current = null
    setMessages([])
    setDraft("")
    setBusy(false)
    setError("")
    input.current?.focus()
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
            <p>{preview ? "Preview · agent not connected" : "Explore the collection together"}</p>
          </div>
          <button
            type="button"
            className="chat-icon-button"
            aria-label="Close collection chat"
            onClick={close}
          >
            <X size={20} />
          </button>
        </div>
        <div className="chat-toolbar">
          <span className="eyebrow">A LITTLE CURIOSITY GOES A LONG WAY</span>
          <button
            type="button"
            className="chat-icon-button"
            aria-label="New chat"
            title="New chat"
            disabled={!messages.length && !draft}
            onClick={newChat}
          >
            <Plus size={18} />
          </button>
        </div>
        <div className="chat-transcript" ref={transcript}>
          {messages.length === 0 && (
            <div className="chat-welcome">
              <span className="chat-welcome-icon">
                <MessageCircle size={28} strokeWidth={1.4} />
              </span>
              <h3>
                Every object
                <br />
                has a story.
              </h3>
              <p>Start with a question, follow an idea, or see where your curiosity takes you.</p>
              <div className="chat-prompts">
                {prompts.map((prompt) => (
                  <button
                    type="button"
                    key={prompt}
                    onClick={() => {
                      setDraft(prompt)
                      input.current?.focus()
                    }}
                  >
                    {prompt}
                    <ArrowRight size={16} aria-hidden="true" />
                  </button>
                ))}
              </div>
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
                  {message.role === "user" ? "You" : preview ? "Companion · preview" : "Companion"}
                </span>
                <p>{message.content}</p>
              </div>
            ))}
          </div>
          {busy && (
            <p className="chat-pending" role="status">
              <LoaderCircle className="spin" size={14} /> Finding a reply…
            </p>
          )}
          {error && (
            <div className="chat-error" role="alert">
              <p>{error}</p>
              <Button type="button" variant="outline" onClick={() => void reply(messages)}>
                Try again
              </Button>
            </div>
          )}
        </div>
        <form className="chat-composer" onSubmit={submit}>
          <label className="sr-only" htmlFor="chat-message">
            Message the collection companion
          </label>
          <div className="chat-input-wrap">
            <textarea
              id="chat-message"
              ref={input}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
              placeholder="What are you curious about?"
              rows={3}
              maxLength={2000}
              aria-describedby="chat-input-help"
            />
            <Button
              className="chat-send"
              type="submit"
              aria-label="Send message"
              disabled={!draft.trim() || busy || Boolean(error)}
            >
              <ArrowUp size={19} />
            </Button>
          </div>
          <p id="chat-input-help">Enter to send · Shift + Enter for a new line</p>
          {preview && (
            <p className="chat-preview-note">
              Preview only. Messages stay in this page and aren’t sent to an agent.
            </p>
          )}
        </form>
      </aside>
    </>
  )
}
