import { useEffect, useRef, useState } from "react";

const newId = () =>
  ((crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)) + "")
    .replace(/[^A-Za-z0-9_-]/g, "");

const LABELS = {
  check_availability: "Checked availability",
  book_appointment: "Booked appointment",
  lookup_appointment: "Looked up appointments",
  reschedule_appointment: "Rescheduled appointment",
  cancel_appointment: "Cancelled appointment",
  escalate_to_human: "Passed to clinic staff",
};

const STARTERS = [
  "I'd like to book an appointment",
  "I need to reschedule my visit",
  "I want to cancel my appointment",
];

export default function Chat({ onToolCalls }) {
  const [sessionId, setSessionId] = useState(newId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [messages, busy]);

  async function send(text) {
    text = text.trim();
    if (!text || busy || locked) return;
    setInput("");
    setError("");
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
      }
      const data = await res.json();
      const esc = data.tool_calls.find((c) => c.name === "escalate_to_human" && c.result?.ok);
      setMessages((m) => [...m, { role: "assistant", text: data.reply, calls: data.tool_calls,
                                  flag: esc?.result.category }]);
      setLocked(data.locked);
      onToolCalls(data.tool_calls);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function newCall() {
    fetch(`/api/sessions/${sessionId}`, { method: "DELETE" }).catch(() => {});
    setSessionId(newId());
    setMessages([]);
    setLocked(false);
    setError("");
  }

  return (
    <section className="chat">
      <div className="log" role="log" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty">
            <h1>How can the front desk help?</h1>
            <p>Book, move or cancel a visit with Dr. Rao. Clinic hours are Monday to Friday, 9:00 to 17:00.</p>
            <div className="chips">
              {STARTERS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <article key={i} className={`msg ${m.role}${m.flag ? ` flag-${m.flag}` : ""}`}>
            <p>{m.text}</p>
            {m.calls?.length > 0 && (
              <details className="trace">
                <summary>{m.calls.map((c) => LABELS[c.name] ?? c.name).join(", ")}</summary>
                {m.calls.map((c, j) => (
                  <pre key={j}>{c.name}({JSON.stringify(c.input)}){"\n→ "}{JSON.stringify(c.result, null, 1)}</pre>
                ))}
              </details>
            )}
          </article>
        ))}
        {busy && <article className="msg assistant pending"><p>Working on it…</p></article>}
        <div ref={endRef} />
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      {locked && (
        <p className="note">This call was handed to clinic staff.{" "}
          <button className="link" onClick={newCall}>Start a new call</button></p>
      )}

      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} disabled={locked}
               placeholder={locked ? "Conversation ended" : "Type your message"} aria-label="Message" maxLength={2000} />
        <button type="submit" disabled={busy || locked || !input.trim()}>Send</button>
        <button type="button" className="ghost" onClick={newCall}>New call</button>
      </form>
    </section>
  );
}
