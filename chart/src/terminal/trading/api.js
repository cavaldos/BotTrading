// Client for the live bot's trading API (src/live/web/server.py).
// The Vite server proxies /api to the bot; every call carries the WEB_API_TOKEN.
import { loadJSON, removeKey, saveJSON } from '../lib/storage';

const TOKEN_KEY = 'tradeToken';

export const getToken = () => loadJSON(TOKEN_KEY, '') || '';
export const setToken = (token) => saveJSON(TOKEN_KEY, token);
export const clearToken = () => removeKey(TOKEN_KEY);

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

const UNREACHABLE = 'Bot is not reachable. Is it running with web.enabled: true?';

async function request(path, { method = 'GET', body, signal, token = getToken() } = {}) {
  const headers = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
      cache: 'no-store',
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError(UNREACHABLE, 0);
  }
  return res;
}

/** JSON call. Throws ApiError with the bot's message on any non-2xx answer. */
export async function api(path, options) {
  const res = await request(path, options);
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const data = isJson ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    // The dev-server proxy answers 5xx with plain text when the bot is down
    const message = res.status === 401
      ? 'Token rejected by the bot'
      : data?.error || (isJson ? `HTTP ${res.status}` : UNREACHABLE);
    throw new ApiError(message, res.status);
  }
  return data;
}

/** Object URL of a signal's chart image (fetched with the token). Revoke it when done. */
export async function apiImageUrl(path, options) {
  const res = await request(path, options);
  if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
  return URL.createObjectURL(await res.blob());
}

/**
 * Read the server-sent event stream. EventSource cannot send an Authorization
 * header, so the stream is read with fetch. Resolves when the stream ends.
 * `onEvent(name, data)` also receives ('ping', null) on keep-alive comments.
 */
export async function readEvents(onEvent, { signal, token = getToken() } = {}) {
  const res = await request('/events', { signal, token });
  if (!res.ok || !res.body) {
    throw new ApiError(res.status === 401 ? 'Token rejected by the bot' : UNREACHABLE, res.status);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let cut = buffer.indexOf('\n\n');
    while (cut >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      cut = buffer.indexOf('\n\n');
      let name = 'message';
      const lines = [];
      chunk.split('\n').forEach(line => {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart());
      });
      if (!lines.length) {
        onEvent('ping', null);
        continue;
      }
      let data;
      try {
        data = JSON.parse(lines.join('\n'));
      } catch {
        continue;
      }
      onEvent(name, data);
    }
  }
}

export function newClientId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
