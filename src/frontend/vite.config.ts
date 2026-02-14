import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  plugins: [react()],
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
