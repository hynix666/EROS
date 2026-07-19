import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy: the LIL owns everything; the UI is a pure client of it.
const backend = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/research': backend,
      '/runs': backend,
      '/api': backend,
      '/lil': backend,
      '/health': backend,
      '/ws': { target: backend, ws: true },
    },
  },
});
