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
    '/api': {
      target: botApi,
      changeOrigin: true,
      // Bot down: answer with the reason as JSON (the chart shows `error`) instead of an empty 500
      configure: (server) => {
        server.on('error', (err, _req, res) => {
          if (!res || typeof res.writeHead !== 'function' || res.headersSent || res.writableEnded) return
          const error = `Chart server cannot reach the bot API at ${botApi} (${err.code || err.message}). ` +
            'On the server check: the bot is running, web.enabled: true in src/config/live.yaml, ' +
            'WEB_API_TOKEN (16+ characters) in .env, and web.port matches. ' +
            'Run on the server: .venv/bin/python -m src.live.web.doctor'
          res.writeHead(502, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ error }))
        })
      },
    },
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
