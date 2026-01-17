import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default ({ mode }) => {
  // Load env variables from .env files for the current `mode`
  // Set `VITE_ALLOWED_HOSTS` in your .env (comma-separated, e.g. "*,.,1m-usd.nport.link,smc.nport.link")
  const env = loadEnv(mode, process.cwd(), '')
  const rawHosts = env.VITE_ALLOWED_HOSTS || '*'
  const allowedHosts = rawHosts
    .split(',')
    .map(h => h.trim())
    .filter(Boolean)

  return defineConfig({
    plugins: [react(), tailwindcss()],
    css: {
      transformer: 'postcss', // Sử dụng PostCSS thay vì LightningCSS để tương thích với Tailwind CSS v4
    },
    server: {
      host: '0.0.0.0',
      allowedHosts,
    },
    preview: {
      host: '0.0.0.0',
      allowedHosts,
      port: 5173,
    },
  })
}
