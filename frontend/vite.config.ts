/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.GBCFMS_API ?? "http://localhost:8000", changeOrigin: false } },
  },
  build: {
    rollupOptions: {
      output: { manualChunks: { react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"], charts: ["recharts"] } },
    },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
