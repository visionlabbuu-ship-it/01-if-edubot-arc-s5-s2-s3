import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// โปรซี /api → sim :8770 ให้ browser เรียก same-origin ได้
// (กัน CORS / WSL / 127.0.0.1 กับ localhost คนละ host)
const SIM_TARGET = process.env.VITE_ARC_SIM_URL || 'http://127.0.0.1:8770'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': '/src',
      '~': '/src',
    },
  },
  server: {
    host: true,
    port: 3000,
    proxy: {
      '/api': {
        target: SIM_TARGET,
        changeOrigin: true,
      },
    },
  },
})
