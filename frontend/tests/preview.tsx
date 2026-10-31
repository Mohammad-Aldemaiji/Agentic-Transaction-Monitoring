// Development-only visual fixture. Not an entry point in the production build.
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { fixture, response } from "./fixtures";
import "../src/style.css";
sessionStorage.setItem("rasid.csrf", "test-preview-only");
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = String(input);
  if (!path.startsWith("/api/v1/") || (init?.method && init.method !== "GET"))
    return response({ error: { code: "preview_is_read_only" } }, 403);
  return response(fixture(path.replace("/api/v1", "")));
};
createRoot(document.getElementById("root")!).render(
  <>
    <div className="preview-banner">
      UI TEST PREVIEW · Synthetic fixtures · Writes and external requests
      blocked
    </div>
    <App />
  </>,
);
