import { useEffect, useRef, useState } from "react";
import { API_URL, onEvent } from "../api";
import Panel from "./Panel";

type ToolCall = { id: string; function: { name: string; arguments: string } };
type Message = {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: ToolCall[];
  name?: string;
};

const SUGGESTIONS = [
  "Prepare order #1042 and move it to the dispatch area",
  "Which orders are waiting for fulfillment?",
  "Are there any open exceptions?",
];

function conversationId() {
  try {
    const existing = localStorage.getItem("conversation_id");
    if (existing) return existing;
    const created = crypto.randomUUID();
    localStorage.setItem("conversation_id", created);
    return created;
  } catch {
    return crypto.randomUUID();
  }
}

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
  );
}

function MessageView({ message }: { message: Message }) {
  if (message.role === "tool") return null;
  const isEvent = message.role === "user" && message.content.startsWith("[SYSTEM EVENT]");
  return (
    <div className={`msg ${isEvent ? "event" : message.role}`}>
      {message.content && (
        <div className="msg-text">
          {message.content.split("\n").map((line, i) => (
            <div key={i}>{line ? renderInline(line) : " "}</div>
          ))}
        </div>
      )}
      {message.tool_calls?.map((call) => (
        <code key={call.id} className="tool-chip" title={call.function.arguments}>
          {call.function.name}({Object.values(JSON.parse(call.function.arguments || "{}")).join(", ")})
        </code>
      ))}
    </div>
  );
}

export default function ChatPanel() {
  const [conversation, setConversation] = useState(conversationId);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const base = `${API_URL}/api/agent/conversations/${conversation}/messages`;

  useEffect(() => {
    const load = () =>
      fetch(base)
        .then((res) => res.json())
        .then(setMessages)
        .catch(() => undefined);
    load();
    return onEvent((event) => {
      if (event.entity === "agent_messages" && event.entity_id === conversation) load();
    });
  }, [base, conversation]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, busy]);

  const send = async (content: string) => {
    if (!content.trim() || busy) return;
    setInput("");
    setBusy(true);
    try {
      await fetch(base, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content }),
      });
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    const fresh = crypto.randomUUID();
    try {
      localStorage.setItem("conversation_id", fresh);
    } catch {}
    setConversation(fresh);
  };

  return (
    <Panel title="Operations Agent" area="chat">
      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && (
          <div className="suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m) => (
          <MessageView key={m.id} message={m} />
        ))}
        {busy && <div className="msg assistant muted">Working…</div>}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. Prepare order #1042 and move it to dispatch"
          disabled={busy}
        />
        <button disabled={busy || !input.trim()}>Send</button>
        <button type="button" className="secondary" onClick={reset} title="New conversation">
          New
        </button>
      </form>
    </Panel>
  );
}
