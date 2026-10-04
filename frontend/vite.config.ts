import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In dev (`npm run dev`), API calls are proxied to the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': process.env.API_URL ?? 'http://localhost:8000' },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          mantine: ['@mantine/core', '@mantine/hooks', '@mantine/notifications'],
          codemirror: ['@uiw/react-codemirror', '@codemirror/lang-sql'],
        },
      },
    },
  },
});
