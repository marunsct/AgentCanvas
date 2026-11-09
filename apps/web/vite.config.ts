/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Settings › About shows what this bundle is: the release in package.json, and the commit when the build is given
// GIT_SHA (the web image passes its build argument)
const release = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).version as string;

// During development (and `vite preview`), /api/* is forwarded to the Python API on :8000 (E2E_API_PORT moves it,
// for a machine where another program already holds 8000)
const api = { "/api": { target: `http://localhost:${process.env.E2E_API_PORT ?? 8000}`, rewrite: (p: string) => p.replace(/^\/api/, "") } };

// The production Content-Security-Policy (infra/web/20-csp.envsh) without the identity provider's origin: the
// end-to-end suite runs against `vite preview` with it in CI, so a page that needs eval or inline script fails there.
export const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; " +
  "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'self'; " +
  "form-action 'self'; object-src 'none'";

// S23: share-link chat pages (/s/…) may be framed by any site (the chat widget); every other page only by itself
export const CSP_EMBED = CSP.replace("frame-ancestors 'self'", "frame-ancestors *");

/** `vite preview` headers like the production nginx: the CSP (embeddable on /s/) and X-Frame-Options elsewhere. */
const securityHeaders: Plugin = {
  name: "agentcanvas-security-headers",
  configurePreviewServer(server) {
    server.middlewares.use((req, res, next) => {
      const path = (req.url ?? "/").split("?")[0];
      const share = path.startsWith("/s/");
      res.setHeader("Content-Security-Policy", share ? CSP_EMBED : CSP);
      if (!share) res.setHeader("X-Frame-Options", "SAMEORIGIN");
      next();
    });
  },
};

export default defineConfig({
  plugins: [react(), securityHeaders],
  define: { __APP_VERSION__: JSON.stringify(release), __APP_COMMIT__: JSON.stringify(process.env.GIT_SHA ?? "") },
  build: {
    // pages are lazy chunks; the only chunk over 500 kB is ELK (~1.4 MB), fetched on the first "Tidy"
    chunkSizeWarningLimit: 1500,
    // minifying drops the licence comments: every bundled package's licence goes in this file, served beside the app
    license: { fileName: "third-party-licenses.txt" },
  },
  server: { proxy: api },
  preview: { proxy: api },
  test: { include: ["src/**/*.test.{ts,tsx}"] },
});
