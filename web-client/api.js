// Only the WEB API participates in client authentication.
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
