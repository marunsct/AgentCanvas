/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development (and `vite preview`), /api/* is forwarded to the Python API on :8000
const api = { "/api": { target: "http://localhost:8000", rewrite: (p: string) => p.replace(/^\/api/, "") } };

// The production Content-Security-Policy (infra/web/20-csp.envsh) without the identity provider's origin: the
// end-to-end suite runs against `vite preview` with it in CI, so a page that needs eval or inline script fails there.
export const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; " +
  "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'self'; " +
  "form-action 'self'; object-src 'none'";

export default defineConfig({
  plugins: [react()],
  // pages are lazy chunks; the only chunk over 500 kB is ELK (~1.4 MB), fetched on the first "Tidy"
  build: { chunkSizeWarningLimit: 1500 },
  server: { proxy: api },
  preview: { proxy: api, headers: { "Content-Security-Policy": CSP } },
  test: { include: ["src/**/*.test.{ts,tsx}"] },
});
