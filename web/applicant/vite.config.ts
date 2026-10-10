// Сборка страницы соискателя: статика, которую отдаёт Caddy с корня сайта.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
});
