import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.BACKEND_PROXY ?? "http://localhost:8010";
const sim = process.env.SIM_PROXY ?? "http://localhost:8020";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": backend,
      "/ws": { target: backend, ws: true },
      "/sim": { target: sim, rewrite: (path) => path.replace(/^\/sim/, "") },
    },
  },
});
