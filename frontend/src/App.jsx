import { useCallback, useState } from "react";
import Chat from "./Chat.jsx";
import Schedule from "./Schedule.jsx";

const MUTATING = ["book_appointment", "reschedule_appointment", "cancel_appointment"];

export default function App() {
  const [screen, setScreen] = useState("desk");
  const [refreshKey, setRefreshKey] = useState(0); // bumps after every tool call
  const [changes, setChanges] = useState({ ids: [], focusDate: null, stamp: 0 });

  const onToolCalls = useCallback((calls) => {
    if (!calls.length) return;
    setRefreshKey((k) => k + 1);
    const changed = calls.filter((c) => MUTATING.includes(c.name) && c.result?.ok);
    if (!changed.length) return;
    const appts = changed.map((c) => c.result.appointment || c.result.cancelled).filter(Boolean);
    const last = appts[appts.length - 1];
    setChanges({
      ids: changed.filter((c) => c.name !== "cancel_appointment").map((c) => c.result.appointment?.id),
      focusDate: last?.starts_at ?? null,
      stamp: Date.now(),
    });
  }, []);

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <strong>Dr. Rao's clinic</strong>
          <span>Front desk</span>
        </div>
        <nav aria-label="Screens">
          <button aria-current={screen === "desk"} onClick={() => setScreen("desk")}>Front desk</button>
          <button aria-current={screen === "schedule"} onClick={() => setScreen("schedule")}>Schedule</button>
        </nav>
      </header>
      {/* both stay mounted so the conversation survives switching screens */}
      <main hidden={screen !== "desk"}><Chat onToolCalls={onToolCalls} /></main>
      <main hidden={screen !== "schedule"}>
        <Schedule refreshKey={refreshKey} changes={changes} />
      </main>
    </div>
  );
}
