import { defineConfig, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// CRA-era sources keep JSX inside .js files. Vite's esbuild pipeline parses
// .js with the plain `js` loader (no JSX), and @vitejs/plugin-react drops its
// Babel pass during builds — so compile JSX in .js up front here.
const jsWithJsx = {
  name: 'cra-js-with-jsx',
  enforce: 'pre',
  transform(code, id) {
    if (!/\/src\/.*\.js$/.test(id.split('?')[0])) return null;
    return transformWithEsbuild(code, id, {
      loader: 'jsx',
      jsx: 'automatic',
      jsxImportSource: 'react',
    });
  },
};

// https://vite.dev/config/
export default defineConfig({
  plugins: [jsWithJsx, react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_BASE_URL || 'http://localhost:8001',
        changeOrigin: true,
      },
      '/events': {
        target: process.env.VITE_API_BASE_URL || 'http://localhost:8001',
        changeOrigin: true,
        // No WebSocket upgrades expected on the event path.
        ws: false,
        // Default http-proxy behavior: pipe the upstream response through
        // untouched so Server-Sent Events flush chunk-by-chunk instead of
        // being buffered. Do not set selfHandleResponse to true.
        selfHandleResponse: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
    // Components build API URLs from this var (see src/test/handlers.js) — pin
    // it so handler paths and component paths derive from the same base.
    env: { VITE_API_BASE_URL: 'http://localhost:8001' },
  },
});
