import { AuthError, webAuth } from './api.js';
import { mountWorkspace } from './workspace.js';
import { setupAppearance } from './appearance.js';

const workspace = mountWorkspace(document.getElementById('session-panel'));
setupAppearance(document.getElementById('session-panel'));

const byId = id => document.getElementById(id);
const form = byId('login-form');
const login = byId('login');
const password = byId('password');
const loginButton = byId('login-button');
const logoutButton = byId('logout-button');
let busy = false;
let retryUntil = 0;
let retryTimer;

function showPanel(name, focus = true) {
  if (name !== 'session') {
    if (!document.getElementById('session-panel').hidden) workspace.reset();
    for (const dialog of document.querySelectorAll('#session-panel dialog[open]')) dialog.close();
  }
  for (const panel of document.querySelectorAll('.card')) panel.hidden = panel.id !== `${name}-panel`;
  if (name === 'session') workspace.loadMenu();
  if (focus) byId(`${name}-panel`).querySelector('h1')?.focus();
}

function message(id, text = '') {
  const area = byId(id);
  area.textContent = text;
  area.hidden = !text;
}

function updateLoginButton() {
  clearTimeout(retryTimer);
  const seconds = Math.max(0, Math.ceil((retryUntil - Date.now()) / 1000));
  loginButton.disabled = busy || seconds > 0;
  loginButton.textContent = busy ? 'Входим…' : seconds
    ? `Повторить через ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : 'Войти';
  if (seconds > 0) retryTimer = setTimeout(updateLoginButton, 1000);
}

function clearForm() {
  form.reset();
  password.value = '';
  login.removeAttribute('aria-invalid');
  password.removeAttribute('aria-invalid');
  message('login-error');
}

function loginError(error) {
  if (!(error instanceof AuthError)) return 'Сервис временно недоступен. Попробуйте ещё раз чуть позже.';
  switch (error.status) {
    case 401: return 'Неверный логин или пароль.';
    case 429: return 'Слишком много попыток входа. Подождите перед следующей попыткой.';
    case 422: return 'Проверьте правильность заполнения логина и пароля.';
    case 403: return 'Не удалось проверить безопасность запроса. Обновите страницу и попробуйте снова.';
    default: return 'Сервис временно недоступен. Попробуйте ещё раз чуть позже.';
  }
}

async function checkSession() {
  if (busy) return;
  busy = true;
  showPanel('loading', false);
  clearForm();
  try {
    await webAuth.me();
    showPanel('session');
  } catch (error) {
    showPanel(error instanceof AuthError && error.status === 401 ? 'login' : 'unavailable');
  } finally {
    busy = false;
    updateLoginButton();
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || Date.now() < retryUntil) return;
  message('login-error');
  login.removeAttribute('aria-invalid');
  password.removeAttribute('aria-invalid');
  if (!login.value.trim() || !password.value) {
    const empty = !login.value.trim() ? login : password;
    empty.setAttribute('aria-invalid', 'true');
    message('login-error', 'Введите логин и пароль.');
    empty.focus();
    return;
  }
  busy = true;
  byId('login-fields').disabled = true;
  form.setAttribute('aria-busy', 'true');
  updateLoginButton();
  try {
    const pending = webAuth.login(login.value.trim(), password.value);
    password.value = '';
    await pending;
    clearForm();
    retryUntil = 0;
    message('logout-error');
    showPanel('session');
  } catch (error) {
    message('login-error', loginError(error));
    if (error instanceof AuthError && [401, 429].includes(error.status)) retryUntil = error.retryUntil;
  } finally {
    password.value = '';
    busy = false;
    byId('login-fields').disabled = false;
    form.removeAttribute('aria-busy');
    updateLoginButton();
  }
});

logoutButton.addEventListener('click', async () => {
  if (busy) return;
  busy = true;
  logoutButton.disabled = true;
  logoutButton.textContent = 'Выходим…';
  message('logout-error');
  try {
    await webAuth.logout();
    clearForm();
    showPanel('login');
  } catch (error) {
    if (error instanceof AuthError && error.status === 401) {
      clearForm();
      showPanel('login');
    } else {
      message('logout-error', error instanceof AuthError && error.status === 403
        ? loginError(error) : 'Не удалось выйти. Попробуйте ещё раз чуть позже.');
    }
  } finally {
    busy = false;
    logoutButton.disabled = false;
    logoutButton.textContent = 'Выйти';
    updateLoginButton();
  }
});

byId('retry-button').addEventListener('click', checkSession);
byId('session-panel').addEventListener('web-session-expired', checkSession);
window.addEventListener('pageshow', event => { if (event.persisted) checkSession(); });
checkSession();
