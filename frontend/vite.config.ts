import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // 前端只跟本机后端说话：/api 全量转发给 FastAPI，前端代码里不出现端口与跨域问题
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 语音会话的 WebSocket 也要代理，否则前端只能直连 8000 端口
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
