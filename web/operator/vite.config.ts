// Рабочее место оператора: статика под /operator/, API — тот же origin (/api/*), без внешних ресурсов.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/operator/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
});
