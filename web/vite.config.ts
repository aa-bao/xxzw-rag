import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: './',
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    allowedHosts: [
      'esophagus-truffle-askew.ngrok-free.dev',
      '.cpolar.top',
      '.cpolar.cn'
    ],
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/platform': 'http://127.0.0.1:8000',
    },
  },
})
