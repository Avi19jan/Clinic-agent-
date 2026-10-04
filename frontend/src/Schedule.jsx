import { useEffect, useState } from "react";

const parse = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const toIso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
// Monday of the week containing d; on weekends, the coming Monday
const weekStart = (d) => {
  const x = new Date(d); const wd = x.getDay();
  x.setDate(x.getDate() + (wd === 0 ? 1 : wd === 6 ? 2 : 1 - wd));
  return toIso(x);
};
const shift = (iso, days) => { const d = parse(iso); d.setDate(d.getDate() + days); return toIso(d); };
const CATEGORY = {
  medical_emergency: "Medical emergency", medical_advice: "Asked for medical advice", abusive: "Abusive caller",
  suspicious_identity: "Identity check failed", out_of_scope: "Outside scheduling",
};

export default function Schedule({ refreshKey, changes }) {
  const [start, setStart] = useState(() => weekStart(new Date()));
  const [grid, setGrid] = useState(null);
  const [escalations, setEscalations] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => { if (changes.focusDate) setStart(weekStart(parse(changes.focusDate))); }, [changes.stamp]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [s, e] = await Promise.all([
          fetch(`/api/schedule?start=${start}&days=5`).then((r) => { if (!r.ok) throw new Error(); return r.json(); }),
          fetch("/api/escalations?limit=8").then((r) => r.json()),
        ]);
        if (live) { setGrid(s.days); setEscalations(e); setError(""); }
      } catch { if (live) setError("Couldn't load the schedule. Is the API running on port 8000?"); }
    })();
    return () => { live = false; };
  }, [start, refreshKey]);

  const booked = grid ? grid.reduce((n, d) => n + d.slots.filter((s) => s.appointment).length, 0) : 0;
  const fmt = (iso) => parse(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

  return (
    <section className="schedule">
      <div className="bar">
        <h1>{grid ? `${fmt(start)} – ${fmt(shift(start, 4))}` : "Schedule"}</h1>
        <span className="count">{booked} booked this week</span>
        <div className="pager">
          <button onClick={() => setStart(shift(start, -7))}>Previous week</button>
          <button onClick={() => setStart(weekStart(new Date()))}>This week</button>
          <button onClick={() => setStart(shift(start, 7))}>Next week</button>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      <div className="week">
        {grid?.map((day) => (
          <div className="day" key={day.date}>
            <h2>{fmt(day.date)}</h2>
            <ul>
              {day.slots.map((s) => {
                const a = s.appointment;
                const flash = a && changes.ids.includes(a.id);
                return (
                  <li key={s.starts_at + (flash ? changes.stamp : "")}
                      className={`slot ${a ? "booked" : "open"}${flash ? " flash" : ""}`}>
                    <time>{s.starts_at.slice(11)}</time>
                    {a ? <span><strong>{a.patient_name}</strong><small>{a.reason}</small></span>
                       : <span className="muted">Open</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className="escalations">
        <h2>Handed to staff</h2>
        {escalations.length === 0 ? <p className="muted">Nothing yet. Calls the assistant can't handle will appear here.</p> : (
          <ul>
            {escalations.map((e) => (
              <li key={e.id} className={e.urgency === "urgent" ? "urgent" : ""}>
                <strong>{CATEGORY[e.category] ?? e.category}</strong>
                <span>{e.summary}</span>
                <time>{e.created_at} UTC</time>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
