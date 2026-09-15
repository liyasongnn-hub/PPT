import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  base: '',
  // Keep the dependency optimizer cache outside node_modules on Windows.
  cacheDir: '.vite-cache',
  plugins: [
    vue(),
  ],
  server: {
    host: '127.0.0.1',
    port: 5174,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:6801',
        changeOrigin: true,
        // Platform routes already include /api/v1 on the backend. Legacy
        // generation routes remain mounted at the root for compatibility.
        rewrite: (path) => path.startsWith('/api/v1') ? path : path.replace(/^\/api/, ''),
      }
    }
  },
  css: {
    preprocessorOptions: {
      scss: {
        additionalData: `
          @import '@/assets/styles/variable.scss';
          @import '@/assets/styles/mixin.scss';
        `
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  }
})
