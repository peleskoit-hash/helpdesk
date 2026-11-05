import { notifyAttachmentTooLarge } from './api.js?v=20261105-web-file-size-notice-v1';

// No File/Blob is retained or sent: only a rejected selection's metadata.
export function createFileSizeNotices(error, onSaved) {
  let owner = null, active = false, generation = 0;
  const pending = new Set();
  function eventId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function renderFailure() {
    const failures = [...pending].filter(entry => entry.failed);
    const previous = error.querySelector('[data-size-notice-warning]');
    const events = failures.map(entry => entry.body.event_id).join(',');
    if (previous && previous.dataset.events === events) return;
    previous?.remove();
    if (!failures.length) { if (!error.textContent.trim()) error.hidden = true; return; }
    const warning = document.createElement('span'); warning.dataset.sizeNoticeWarning = ''; warning.dataset.events = events;
    warning.textContent = `${error.textContent ? '\n' : ''}Не удалось подтвердить сохранение уведомления о файлах больше 20 МиБ: ${failures.map(e => e.body.file_name).join(', ')}. Эти файлы не загружены. `;
    const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Повторить уведомление';
    retry.addEventListener('click', () => { for (const entry of failures) send(entry); });
    warning.append(retry); error.append(warning); error.hidden = false;
  }
  async function send(entry) {
    if (!active || entry.controller || !pending.has(entry)) return;
    const current = generation, controller = new AbortController(); entry.controller = controller;
    try {
      await notifyAttachmentTooLarge(entry.body, controller.signal);
      if (controller.signal.aborted || current !== generation) return;
      pending.delete(entry); renderFailure(); onSaved();
    } catch {
      if (controller.signal.aborted || current !== generation) return;
      entry.failed = true; renderFailure();
    } finally { if (entry.controller === controller) entry.controller = null; }
  }
  function pause() {
    active = false; generation++;
    for (const entry of pending) { entry.controller?.abort(); entry.controller = null; }
  }
  return {
    start(id) {
      if (owner && owner !== id) this.clear();
      if (active && owner === id) return;
      owner = id; active = true;
      for (const entry of pending) send(entry);
    },
    reject(name, size) {
      const entry = { body: { file_name: name, file_size: size, event_id: eventId() } };
      pending.add(entry); send(entry);
    },
    pause, renderFailure,
    clear() { pause(); pending.clear(); owner = null; renderFailure(); },
  };
}
