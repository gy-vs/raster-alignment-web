import { defineConfig } from "vite";

// Dev server proxies /api to the local FastAPI backend.
// `npm run build` emits static files into ../backend/static, which the
// backend can serve directly (see backend/app/server.py / README).
export default defineConfig({
  plugins: [],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../backend/static",
    emptyOutDir: true,
  },
});
