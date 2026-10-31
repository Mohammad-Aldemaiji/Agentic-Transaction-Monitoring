import { Component, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./style.css";
// Email confirmation may return provider tokens in the URL. Sign in explicitly;
// never retain those tokens in browser history or persistent application storage.
if (/(?:access_token|refresh_token)=/.test(window.location.hash)) {
  window.history.replaceState(
    null,
    "",
    window.location.pathname + window.location.search,
  );
}
class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="empty">
        <h1>The workspace could not be displayed</h1>
        <p>Reload to recover your session. Unsaved form content may be lost.</p>
        <button onClick={() => location.reload()}>Reload workspace</button>
      </main>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);
