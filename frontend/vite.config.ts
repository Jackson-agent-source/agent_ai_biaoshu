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

    // ------------------------------------------------------------------
    // AI 配图的本地代理（★ 不加这段，本地开发时图片一定显示不出来）
    // ------------------------------------------------------------------
    // 后端返回的图片地址是**相对路径** `/media/images/2026/09/xxx.jpg`，
    // 这和线上的地址一字不差（线上由 nginx 的 /media/ 直接发文件）。
    //
    // 但本地开发时前端跑在 5173、后端在 8000，是**两个源**。
    // 相对路径会被浏览器按当前页面解析成
    //     http://localhost:5173/media/...
    // 打到 Vite 自己身上 —— Vite 上没有这个文件，于是 404，
    // 页面上就是一片裂图。而后端日志里什么都没有，
    // 因为请求压根没发到后端，很容易误判成"图片没生成成功"。
    //
    // 加了这个代理，Vite 把 /media 转给 Django，问题消失。
    // 生产构建时这段完全不参与（server 只在 dev server 生效），
    // 所以不会有任何线上影响。
    //
    // ★ 注意 /api 不需要代理：前端走的是 VITE_API_BASE_URL 里的
    //   绝对地址 http://localhost:8000/api/v1，本来就直连后端。
    //   只有图片是相对路径，所以只有它需要这一段。
    proxy: {
      '/media': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
