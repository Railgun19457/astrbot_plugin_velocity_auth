import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import vuetify from "vite-plugin-vuetify";

// 插件页面通过带 token 的子路径访问，所有产物资源必须保持相对路径。
export default defineConfig({
  base: "./",
  plugins: [vue(), vuetify({ autoImport: true })],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../pages/dashboard",
    emptyOutDir: true,
    // 单页单包，便于在沙箱 iframe 里加载
    chunkSizeWarningLimit: 1500,
  },
});
