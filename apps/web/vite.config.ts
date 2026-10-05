/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development, /api/* is forwarded to the Python API on :8000
export default defineConfig({
  plugins: [react()],
  // pages are lazy chunks; the only chunk over 500 kB is ELK (~1.4 MB), fetched on the first "Tidy"
  build: { chunkSizeWarningLimit: 1500 },
  server: { proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") } } },
  test: { include: ["src/**/*.test.{ts,tsx}"] },
});
