// Development-only visual fixture. Not an entry point in the production build.
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { fixture, response } from "./fixtures";
import { applyTheme, initialTheme } from "../src/ui";
import "../src/fonts";
import "../src/style.css";
const query = new URLSearchParams(location.search);
const team = query.get("role") === "supervisor";
if (!query.has("signed-out"))
  sessionStorage.setItem("rasid.csrf", "test-preview-only");
else sessionStorage.clear();
if (query.get("theme"))
  localStorage.setItem("rasid.theme", query.get("theme")!);
applyTheme(initialTheme());
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = String(input);
  if (!path.startsWith("/api/v1/") || (init?.method && init.method !== "GET"))
    return response({ error: { code: "preview_is_read_only" } }, 403);
  const route = path.replace("/api/v1", "");
  const data = fixture(route) as Record<string, unknown>;
  // ?role=supervisor previews the team-wide view with live traffic numbers.
  if (team && route === "/me") return response({ ...data, role: "supervisor" });
  if (team && route.startsWith("/dashboard"))
    return response({
      ...data,
      decided_today: 9,
      transactions: {
        received: 248,
        received_today: 248,
        received_last_hour: 41,
        scored: 246,
        pending: 2,
        failed: 0,
        flagged: 12,
        flagged_last_hour: 3,
        flag_rate: 0.0484,
        average_risk_score: 18.7,
        last_received_at: new Date().toISOString(),
        score_bins_today: [62, 54, 41, 30, 21, 9, 4, 3, 2, 3],
      },
    });
  return response(data);
};
createRoot(document.getElementById("root")!).render(
  <>
    <div className="preview-banner">
      UI test preview with synthetic fixtures. Writes and external requests
      blocked
    </div>
    <App />
  </>,
);
