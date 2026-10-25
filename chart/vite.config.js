import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

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

  // Trading API of the live bot (src/live/web/server.py). The browser only talks to
  // this server; /api is forwarded to the bot, which checks the WEB_API_TOKEN.
  const botApi = env.BOT_API_URL || 'http://127.0.0.1:8787'
  const proxy = {
    '/api': { target: botApi, changeOrigin: true },
  }

  return defineConfig({
    resolve: {
      alias: {
        '@': resolve(__dirname, './src'),
        '@components': resolve(__dirname, './src/components'),
        '@pages': resolve(__dirname, './src/pages'),
        '@hooks': resolve(__dirname, './src/hooks'),
        '@assets': resolve(__dirname, './src/assets'),
        '@plugins': resolve(__dirname, './src/plugins'),
      },
    },
    plugins: [react(), tailwindcss()],
    css: {
      transformer: 'postcss', // Sử dụng PostCSS thay vì LightningCSS để tương thích với Tailwind CSS v4
    },
    server: {
      host: '0.0.0.0',
      allowedHosts,
      proxy,
    },
    preview: {
      host: '0.0.0.0',
      allowedHosts,
      port: 5173,
      proxy,
    },
  })
}
