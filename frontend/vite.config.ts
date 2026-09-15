import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Vite 配置文件
// 文档：https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // 用 @ 代表 src 目录，这样 import 路径更短、更好看
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 启动后自动打开浏览器（如果不想自动打开，把它改成 false 即可）
    open: false,
  },
})
