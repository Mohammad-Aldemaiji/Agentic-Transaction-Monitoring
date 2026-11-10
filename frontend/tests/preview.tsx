// Development-only visual fixture. Not an entry point in the production build.
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { fixture, response } from "./fixtures";
import { applyTheme, initialTheme } from "../src/ui";
import "../src/fonts";
import "../src/style.css";
const query = new URLSearchParams(location.search);
const team = query.get("role") === "supervisor";
const admin = query.get("role") === "admin";
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
  if (admin && route === "/me")
    return response({ ...fixture(route), role: "admin" });
  if (admin && route === "/booth/config")
    return response({ enabled: false, staff_access: true });
  if (admin && route === "/staff/users")
    return response({
      items: [
        {
          id: "preview-analyst",
          name: "Sara Alharbi",
          email: "sara.analyst@example.com",
          role: "analyst",
          active: true,
          face_enrolled: true,
          is_self: false,
          version: 1,
        },
        {
          id: "preview-supervisor",
          name: "Omar Alsalem",
          email: "omar.supervisor@example.com",
          role: "supervisor",
          active: true,
          face_enrolled: true,
          is_self: false,
          version: 1,
        },
        {
          id: "preview-admin",
          name: "Administrator",
          email: "admin@example.com",
          role: "admin",
          active: true,
          face_enrolled: true,
          is_self: true,
          version: 1,
        },
      ],
    });
  if (query.has("assessment") && route.includes("/findings?latest_ai=true"))
    return response({
      items: [
        {
          id: 101,
          kind: "investigation.finding",
          created_at: "2026-11-10T12:00:00Z",
          details: {
            source: "local_llm",
            model: "qwen3:4b",
            evidence_version: 4,
            summary:
              "This payment combines an unusual amount with a new beneficiary. A known device provides some reassuring context; verify the account holder before recommending an action.",
            recommendation: "CONTACT_CUSTOMER",
            analysis: {
              suspected_pattern: "Unusual transfer",
              recommended_action: "VERIFY_WITH_CUSTOMER",
              recommendation_reason:
                "Confirm whether the account holder initiated the transfer.",
              evidence_for_fraud: [
                "The amount is unusual for this account.",
                "The beneficiary has not appeared in the available history.",
              ],
              evidence_against_fraud: [
                "The transaction used a previously recorded device.",
              ],
            },
          },
        },
      ],
    });
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
