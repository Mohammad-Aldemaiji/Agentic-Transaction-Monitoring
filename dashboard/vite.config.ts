import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the dashboard runs on :5173 and forwards API calls to the backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
