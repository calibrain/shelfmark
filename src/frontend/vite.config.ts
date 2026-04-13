import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    strictPort: true,
    cors: true,
    proxy: {
      // Proxy API requests to the Docker backend
      '/api': {
        target: 'http://localhost:8084',
        changeOrigin: true,
        secure: false,
      },
      // Proxy Socket.IO so websocket polling/upgrades share the same origin/cookies as /api.
      '/socket.io': {
        target: 'http://localhost:8084',
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
}));
