import { createContext, useContext, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ANALYST_KEY, Health, Stats, usePoll } from "./api";
import Alerts from "./pages/Alerts";
import CaseView from "./pages/CaseView";
import Feed from "./pages/Feed";
import Labels from "./pages/Labels";
import Phone from "./pages/Phone";

const AnalystContext = createContext<string>("");
export const useAnalyst = () => useContext(AnalystContext);

function readName() {
  try { return localStorage.getItem(ANALYST_KEY) ?? ""; } catch { return ""; }
}

export default function App() {
  const loc = useLocation();
  // The customer's phone is its own full-screen page, opened in a second window for the demo.
  if (loc.pathname.startsWith("/phone")) return <Phone />;
  return <Console />;
}

function Console() {
  const [analyst, setAnalyst] = useState(readName);
  const { data: health } = usePoll<Health>("/api/health", 10000);
  const { data: stats } = usePoll<Stats>("/api/stats", 3000);
  const save = (v: string) => {
    setAnalyst(v);
    try { localStorage.setItem(ANALYST_KEY, v); } catch { /* storage unavailable */ }
  };
  const needs = stats?.cases_by_status?.AWAITING_APPROVAL ?? 0;
  const voiceMissing = health && Object.entries(health.voice).filter(([, v]) => !v.installed).map(([k]) => k);

  return (
    <AnalystContext.Provider value={analyst.trim()}>
      <header className="bar">
        <span className="brand">Transaction Monitoring</span>
        <nav>
          <NavLink to="/" end>Alerts{needs > 0 && <span className="count">{needs}</span>}</NavLink>
          <NavLink to="/feed">Live feed</NavLink>
          <NavLink to="/labels">Decisions</NavLink>
          <a href="/phone" target="_blank" rel="noreferrer">Customer phone</a>
        </nav>
        <div className="who">
          {health && (
            <span title={health.llm.error ?? `${health.llm.model} at ${health.llm.host}`}>
              <span className={`dot ${health.llm.ok ? "ok" : "down"}`} />
              {health.llm.model}
            </span>
          )}
          <input
            aria-label="Your name, recorded on every decision"
            placeholder="Your name"
            value={analyst}
            onChange={(e) => save(e.target.value)}
          />
        </div>
      </header>
      {health && !health.llm.ok && (
        <div className="banner">
          The agent is offline: {health.llm.error} Alerts still arrive and you can decide them yourself.
        </div>
      )}
      {voiceMissing && voiceMissing.length > 0 && health?.llm.ok && (
        <div className="banner">
          No local voice for {voiceMissing.join(" and ")}: the phone will show the agent's words without speaking them.
          Install with <code>python -m piper.download_voices en_US-lessac-medium ar_JO-kareem-medium --download-dir src/agent/voices</code>
        </div>
      )}
      <Routes>
        <Route path="/" element={<Alerts stats={stats} />} />
        <Route path="/cases/:id" element={<CaseView />} />
        <Route path="/feed" element={<Feed stats={stats} />} />
        <Route path="/labels" element={<Labels />} />
        <Route path="*" element={<main className="page"><p>Page not found.</p></main>} />
      </Routes>
    </AnalystContext.Provider>
  );
}
