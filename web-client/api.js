// Client-only WEB endpoints; no administrative or Telegram API calls.
const base = '/api/v1/web/auth';
let sessionEnded = false;
const sessionRequests = new Set();
export function stopWebRequests() {
  sessionEnded = true;
  for (const controller of sessionRequests) controller.abort();
}
export function resumeWebRequests() { sessionEnded = false; }
function requireWebSession() {
  if (sessionEnded) throw new TaskError(401, 'Сессия завершена');
}
function checkWebSession(response, signal) {
  if (response.status !== 401 || signal?.aborted) return;
  stopWebRequests();
  globalThis.dispatchEvent?.(new Event('web-session-unauthorized'));
}

export class AuthError extends Error {
  constructor(status, retryUntil = 0) {
    super('WEB authentication request failed');
    this.status = status;
    this.retryUntil = retryUntil;
  }
}

async function request(path, method = 'GET', body) {
  if (path === '/logout' && sessionEnded) throw new AuthError(401);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(base + path, {
      method, body, signal: controller.signal, credentials: 'same-origin',
      mode: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    });
    if (!response.ok) {
      if (path === '/logout') checkWebSession(response, controller.signal);
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
  requireWebSession();
  const response = await fetch('/api/v1/web/menu', {
    signal, credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  checkWebSession(response, signal);
  if (!response.ok) throw new Error('WEB menu unavailable');
  const menu = await response.json();
  if (!Array.isArray(menu?.rows)) throw new Error('Invalid WEB menu');
  return menu;
}

// Use only WEB-session endpoints, including for binary downloads. Never follow a supplied URL.
async function requestWebHistory(path, signal, method = 'GET', file = false) {
  requireWebSession();
  const controller = new AbortController();
  sessionRequests.add(controller);
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timeout = setTimeout(abort, 15000);
  try {
    const response = await fetch('/api/v1/web' + path, {
      method, signal: controller.signal, credentials: 'same-origin', mode: 'same-origin',
      cache: 'no-store', redirect: 'error',
    });
    checkWebSession(response, controller.signal);
    if (!response.ok) throw new TaskError(response.status, 'Не удалось загрузить результат заявки.');
    return await (file ? response.blob() : response.json());
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
    sessionRequests.delete(controller);
  }
}

export const requestTaskResult = (path, signal, method, file) => requestWebHistory('/task-results' + path, signal, method, file);
export const getWebHistory = (page, signal) => requestWebHistory('/history?page=' + page, signal);
export async function getWebChat(signal) {
  const result = await requestWebHistory('/chat', signal);
  if (!Array.isArray(result?.items) || result.items.length > 50 || result.items.some(row =>
    !row || typeof row.id !== 'string' || !/^\d+$/.test(row.id) || !['client', 'bot', 'system'].includes(row.message_type)
    || typeof row.text !== 'string' || typeof row.created_at !== 'string' || !Number.isFinite(Date.parse(row.created_at)))
    || new Set(result.items.map(row => row.id)).size !== result.items.length) {
    throw new TaskError(502, 'Не удалось проверить историю чата.');
  }
  return result.items;
}
export async function getActiveWebTask(signal) {
  const result = await taskRequest('active', 'GET', undefined, signal);
  if (result !== null && (!uuid.test(result?.task_id) || typeof result.button_id !== 'string'
    || !['collecting', 'confirmation_required'].includes(result.state) || typeof result.text !== 'string'
    || typeof result.expires_at !== 'string' || !Number.isFinite(Date.parse(result.expires_at)))) {
    throw new TaskError(502, 'Не удалось проверить состояние задачи.');
  }
  return result;
}
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

async function taskRequest(path, method, body, signal, upload = false) {
  requireWebSession();
  const controller = new AbortController();
  sessionRequests.add(controller);
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timeout = setTimeout(abort, upload ? 360000 : 15000);
  try {
    const response = await fetch('/api/v1/web/tasks/' + path, {
      method, signal: controller.signal, credentials: 'same-origin', mode: 'same-origin',
      cache: 'no-store', redirect: 'error',
      headers: upload || body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: upload ? body : body === undefined ? undefined : JSON.stringify(body),
    });
    checkWebSession(response, controller.signal);
    const result = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = result?.detail;
      throw new TaskError(response.status, detail?.error?.message || (typeof detail === 'string' ? detail : '')
        || 'Не удалось выполнить запрос. Попробуйте ещё раз.', detail?.error?.code);
    }
    return result;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener('abort', abort);
    sessionRequests.delete(controller);
  }
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export async function sendTaskAction(path, body, signal) {
  const result = await taskRequest(path, 'POST', body, signal);
  if (!result || typeof result.text !== 'string' || !uuid.test(result.task_id)
      || !['collecting', 'confirmation_required', 'ready', 'cancelled'].includes(result.state)) {
    throw new TaskError(502, 'Не удалось проверить ответ сервера. Повторите запрос.');
  }
  return result;
}

const preloadPath = (taskId, id) => encodeURIComponent(taskId) + '/attachment-preloads'
  + (id ? '/' + encodeURIComponent(id) : '');
async function preloadRequest(path, method, body, signal, upload = false) {
  const result = await taskRequest(path, method, body, signal, upload);
  if (!uuid.test(result?.id) || !['uploading', 'ready', 'failed', 'cancelled', 'sending', 'sent'].includes(result.status)) {
    throw new TaskError(502, 'Не удалось проверить состояние файла.');
  }
  return result;
}
export function createTaskPreload(taskId, file, uploadId, signal) {
  const body = new FormData();
  body.append('file', file, file.name);
  body.append('upload_id', uploadId);
  return preloadRequest(preloadPath(taskId), 'POST', body, signal, true);
}
export const getTaskPreload = (taskId, id, signal) => preloadRequest(preloadPath(taskId, id), 'GET', undefined, signal);
export const retryTaskPreload = (taskId, id, confirm, signal) => preloadRequest(preloadPath(taskId, id) + '/retry', 'POST', { confirm_uncertain: confirm }, signal);
export async function deleteTaskPreload(taskId, id, signal) {
  const result = await taskRequest(preloadPath(taskId, id), 'DELETE', undefined, signal);
  if (result?.ok !== true) throw new TaskError(502, 'Не удалось подтвердить удаление файла.');
}
