// Client-only WEB endpoints; no administrative or Telegram API calls.
const base = '/api/v1/web/auth';

export class AuthError extends Error {
  constructor(status, retryUntil = 0) {
    super('WEB authentication request failed');
    this.status = status;
    this.retryUntil = retryUntil;
  }
}

async function request(path, method = 'GET', body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(base + path, {
      method, body, signal: controller.signal, credentials: 'same-origin',
      mode: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    });
    if (!response.ok) {
      const retry = response.headers.get('Retry-After');
      const seconds = retry !== null && /^\d+$/.test(retry.trim()) ? Number(retry) : NaN;
      const until = Number.isFinite(seconds) ? Date.now() + seconds * 1000 : Date.parse(retry);
      throw new AuthError(response.status, Number.isFinite(until) ? until : 0);
    }
    if (response.status === 204) return;
    const result = await response.json();
    if (typeof result?.client_id !== 'string' || !result.client_id) throw new AuthError(502);
    return { client_id: result.client_id };
  } finally {
    clearTimeout(timeout);
  }
}

export const webAuth = {
  me: () => request('/me'),
  login: (login, password) => request('/login', 'POST', JSON.stringify({ login, password })),
  logout: () => request('/logout', 'POST'),
};

export async function getWebMenu(signal) {
  const response = await fetch('/api/v1/web/menu', {
    signal, credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  if (!response.ok) throw new Error('WEB menu unavailable');
  const menu = await response.json();
  if (!Array.isArray(menu?.rows)) throw new Error('Invalid WEB menu');
  return menu;
}

// Use only WEB-session endpoints, including for binary downloads. Never follow a supplied URL.
async function requestWebHistory(path, signal, method = 'GET', file = false) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timeout = setTimeout(abort, 15000);
  try {
    const response = await fetch('/api/v1/web' + path, {
      method, signal: controller.signal, credentials: 'same-origin', mode: 'same-origin',
      cache: 'no-store', redirect: 'error',
    });
    if (!response.ok) throw new TaskError(response.status, 'Не удалось загрузить результат заявки.');
    return await (file ? response.blob() : response.json());
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
}

export const requestTaskResult = (path, signal, method, file) => requestWebHistory('/task-results' + path, signal, method, file);
export const getWebHistory = (page, signal) => requestWebHistory('/history?page=' + page, signal);
export const getHistoryMessages = (id, signal) => requestWebHistory('/history/' + encodeURIComponent(id) + '/messages', signal);
export const getHistoryFile = (ticketId, file, signal) => file.source === 'web_result'
  ? requestTaskResult('/' + encodeURIComponent(ticketId) + '/files/' + encodeURIComponent(file.id), signal, 'GET', true)
  : requestWebHistory('/history/' + encodeURIComponent(ticketId) + '/attachments/' + encodeURIComponent(file.id) + '/download', signal, 'GET', true);

export class TaskError extends Error {
  constructor(status, message, code = '') {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function sendTaskAction(path, body, signal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = setTimeout(abort, 15000);
  try {
    const response = await fetch('/api/v1/web/tasks/' + path, {
      method: 'POST', signal: controller.signal, credentials: 'same-origin', mode: 'same-origin',
      cache: 'no-store', redirect: 'error',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = result?.detail;
      throw new TaskError(response.status, detail?.error?.message || (typeof detail === 'string' ? detail : '')
        || 'Не удалось выполнить запрос. Попробуйте ещё раз.', detail?.error?.code);
    }
    if (!result || typeof result.text !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(result.task_id)
        || !['collecting', 'confirmation_required', 'ready', 'cancelled'].includes(result.state)) {
      throw new TaskError(502, 'Не удалось проверить ответ сервера. Повторите запрос.');
    }
    return result;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
  }
}
