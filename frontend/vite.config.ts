import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8100",
        changeOrigin: true,
        // Adds X-Forwarded-For with the browser's address. uvicorn trusts it from this local proxy, so the
        // backend sees who is calling: without it every caller of the dev server (bound to 0.0.0.0) is 127.0.0.1,
        // shares one rate-limit budget, and would count as the owner's own machine in local admin mode.
        xfwd: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
