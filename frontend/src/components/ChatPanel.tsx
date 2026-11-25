import { useEffect, useRef, useState } from "react";
import { API_URL, onEvent } from "../api";
import Icon from "./Icon";

type ToolCall = { id: string; function: { name: string; arguments: string } };
type Message = {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: ToolCall[];
  name?: string;
};

const SUGGESTIONS = [
  "Prepare order #1045 and move it to dispatch",
  "Cycle count aisle B",
  "Prepare order #1044",
  "Are there any open exceptions?",
];

function newId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function toolArgs(raw: string) {
  try {
    return Object.values(JSON.parse(raw || "{}")).join(", ");
  } catch {
    return raw;
  }
}

function conversationId() {
  try {
    const existing = localStorage.getItem("conversation_id");
    if (existing) return existing;
    const created = newId();
    localStorage.setItem("conversation_id", created);
    return created;
  } catch {
    return newId();
  }
}

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
  );
}

function cells(line: string) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function renderBlocks(text: string) {
  const lines = text.split("\n");
  const blocks: JSX.Element[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      i--;
      const body = rows.filter((row) => !/^\s*\|[\s|:-]+\|\s*$/.test(row));
      const [header, ...data] = body.map(cells);
      blocks.push(
        <table key={i} className="msg-table">
          <thead>
            <tr>{header?.map((cell, c) => <th key={c}>{renderInline(cell)}</th>)}</tr>
          </thead>
          <tbody>
            {data.map((row, r) => (
              <tr key={r}>{row.map((cell, c) => <td key={c}>{renderInline(cell)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      );
    } else {
      blocks.push(<div key={i}>{lines[i] ? renderInline(lines[i]) : " "}</div>);
    }
  }
  return blocks;
}

function MessageView({ message }: { message: Message }) {
  if (message.role === "tool") return null;
  if (message.role === "user" && message.content.startsWith("[SYSTEM EVENT]"))
    return <div className="msg event">{message.content.replace("[SYSTEM EVENT]", "").trim()}</div>;
  if (message.role === "user") return <div className="msg user">{renderBlocks(message.content)}</div>;
  const calls = message.tool_calls ?? [];
  return (
    <div className="msg assistant">
      <span className="avatar">
        <Icon name="cube" size={16} />
      </span>
      <div className="msg-body">
        {message.content && <div className="msg-text">{renderBlocks(message.content)}</div>}
        {calls.length > 0 && (
          <div className="tools">
            {calls.map((call) => (
              <code key={call.id} className="tool-chip" title={call.function.arguments}>
                {call.function.name}
                <span>({toolArgs(call.function.arguments)})</span>
              </code>
            ))}
          </div>
        )}
      </div>
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
        .then((data) => setMessages(Array.isArray(data) ? data : []))
        .catch(() => undefined);
    load();
    return onEvent((event) => {
      if (event.entity === "agent_messages" && event.entity_id === conversation) load();
    });
  }, [base, conversation]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
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
    const fresh = newId();
    try {
      localStorage.setItem("conversation_id", fresh);
    } catch {}
    setConversation(fresh);
  };

  const empty = messages.length === 0;

  return (
    <section className="chat">
      <header className="chat-head">
        <span>Operations Agent</span>
        <button className="ghost" onClick={reset} title="New conversation">
          <Icon name="plus" size={16} /> New chat
        </button>
      </header>
      <div className="chat-log" ref={logRef}>
        <div className="chat-column">
          {empty && (
            <div className="greeting">
              <h1>
                <span className="gradient">Hello, manager</span>
                <br />
                What should the warehouse do?
              </h1>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <MessageView key={m.id} message={m} />
          ))}
          {busy && (
            <div className="msg assistant">
              <span className="avatar thinking">
                <Icon name="cube" size={16} />
              </span>
              <div className="msg-body muted">Thinking…</div>
            </div>
          )}
        </div>
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the warehouse agent"
          disabled={busy}
        />
        <button className="send" disabled={busy || !input.trim()} title="Send">
          <Icon name="send" />
        </button>
      </form>
    </section>
  );
}
