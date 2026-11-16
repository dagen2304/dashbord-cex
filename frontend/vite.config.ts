import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0', // Important pour le conteneur Docker
  },
  resolve: {
    alias: [
      { find: 'lucide-react', replacement: path.resolve(__dirname, 'node_modules/lucide-react/dist/esm/lucide-react.mjs') },
      { find: /^lodash\/(.*)$/, replacement: path.resolve(__dirname, 'node_modules/lodash/$1.js') },
      { find: 'lodash', replacement: path.resolve(__dirname, 'node_modules/lodash/index.js') }
    ]
  }
})
