import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `base: "./"` so a built copy also opens straight off the filesystem.
export default defineConfig({ plugins: [react()], base: "./",
  server: { port: 5173, open: true } });
