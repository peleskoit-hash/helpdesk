import { createDraftPreload, getDraftPreload, retryDraftPreload, deleteDraftPreload, listDraftPreloads, getDraftPreloadContent } from './api.js?v=20261105-web-file-size-notice-v1';

import { createFileSizeNotices } from './file-size-notices.js?v=20261105-web-file-size-notice-v1';

// Server-owned universal files. Only an explicit remove deletes remote resources.
export function createTaskFiles(input, list, error, onChange = () => {}, onNoticeSaved = () => {}) {
  let selected = [], disabled = true, clientId = null, active = false, loaded = false;
  let generation = 0, revision = 0, listing = null, refreshAgain = false;
  const sizeNotices = createFileSizeNotices(error, onNoticeSaved);
  const retired = new Set(), removedIds = new Set(), acceptedIds = new Set(), sentUrls = new Set();
  const images = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
  function fileRow(entry) {
    const row = document.createElement('div');
    row.className = 'task-selected-file';
    row.dataset.status = entry.status;
    const preview = document.createElement(entry.url ? 'img' : 'span');
    if (entry.url) { preview.src = entry.url; preview.alt = entry.file.name; }
    else {
      preview.className = 'task-file-icon';
      preview.setAttribute('aria-hidden', 'true');
      preview.dataset.type = (entry.file.name.split('.').at(-1) || 'FILE').slice(0, 5).toUpperCase();
      preview.title = entry.file.type.startsWith('video/') ? 'Видео' : entry.file.type.startsWith('audio/') ? 'Аудиофайл' : 'Файл';
    }
    const details = document.createElement('div');
    details.className = 'task-file-details';
    const label = document.createElement('span');
    label.className = 'task-file-name'; label.textContent = entry.file.name; label.title = entry.file.name;
    const size = document.createElement('span');
    size.className = 'task-file-size';
    size.textContent = entry.file.size < 1024 ? `${entry.file.size} Б` : entry.file.size < 1024 ** 2
      ? `${(entry.file.size / 1024).toFixed(1)} КБ` : `${(entry.file.size / 1024 ** 2).toFixed(1)} МБ`;
    details.append(label, size); row.append(preview, details);
    return { row, details };
  }
  function button(row, text, action, entry) {
    const control = document.createElement('button');
    control.type = 'button'; control.textContent = text;
    control.disabled = disabled || entry.sending;
    control.addEventListener('click', () => { if (!disabled && !entry.sending) action(); });
    row.append(control); return control;
  }
  function render() {
    list.replaceChildren();
    for (const entry of selected) {
      const { row, details } = fileRow(entry);
      const area = document.createElement('div'); area.className = 'task-file-status';
      const status = document.createElement('div');
      if (entry.status === 'uploading') {
        status.className = 'task-file-loading'; status.textContent = 'Идёт загрузка…';
        const bar = document.createElement('div'); bar.className = 'task-file-progress';
        bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-label', status.textContent);
        area.append(status, bar);
      } else {
        status.className = entry.status === 'ready' ? 'task-file-ready' : 'task-file-failure';
        status.textContent = entry.status === 'ready' ? 'Файл загружен' : entry.uncertain
          ? 'Результат загрузки неизвестен. Повтор может создать лишнюю копию файла.'
          : entry.authError ? 'Сессия истекла. Войдите снова.' : 'Не удалось подготовить файл. Повторите попытку или удалите его.';
        status.title = status.textContent;
        if (entry.status === 'error') status.setAttribute('role', 'alert');
        area.append(status);
      }
      details.append(area);
      const actions = document.createElement('div'); actions.className = 'task-file-actions';
      if (entry.status === 'error') {
        const retry = button(actions, entry.uncertain ? 'Подтвердить повтор' : 'Повторить',
          () => run(entry, entry.id ? 'retry' : 'create', entry.uncertain), entry);
        if (entry.uncertain) retry.setAttribute('aria-label', 'Подтвердить повтор загрузки');
      }
      const remove = button(actions, 'Удалить', () => {
        selected = selected.filter(item => item !== entry); revision++;
        retire(entry); changed();
      }, entry);
      remove.setAttribute('aria-label', `Удалить файл: ${entry.file.name}`);
      row.append(actions); list.append(row);
    }
    list.hidden = !selected.length;
    input.closest('form').dataset.fileRows = String(Math.min(2, selected.length));
  }
  function changed() { render(); sizeNotices.renderFailure(); onChange(); }
  function apply(entry, result) {
    entry.id = result.id; entry.uncertain = result.uncertain === true; entry.authError = false;
    entry.status = entry.uncertain ? 'error' : result.status === 'ready' ? 'ready' : result.status === 'uploading' ? 'uploading' : 'error';
    if (entry.status !== 'uploading') { clearTimeout(entry.timer); entry.timer = null; }
  }
  async function preview(entry) {
    if (entry.url || entry.previewController || !images.has(entry.file.type)) return;
    const current = generation, controller = new AbortController(); entry.previewController = controller;
    try {
      const blob = await getDraftPreloadContent(entry.id, controller.signal);
      if (!controller.signal.aborted && current === generation && !entry.removed && !entry.accepted) {
        entry.url = URL.createObjectURL(blob); changed();
      }
    } catch { /* Keep a file tile if a preview is unavailable; the original stays on the server. */ }
    finally { if (entry.previewController === controller) entry.previewController = null; }
  }
  const pendingIdentity = () => [...selected, ...retired].some(entry => !entry.id);
  async function refresh() {
    if (!active) return;
    if (listing) { refreshAgain = true; return; }
    const current = generation, before = revision, controller = new AbortController(); listing = controller;
    try {
      const rows = await listDraftPreloads(controller.signal);
      if (current !== generation || controller.signal.aborted) return;
      if (before !== revision || pendingIdentity()) { refreshAgain = true; return; }
      const present = new Set(rows.map(row => row.id));
      selected = selected.filter(entry => {
        if (!entry.id || present.has(entry.id) || entry.sending) return true;
        stopEntry(entry); if (entry.url) URL.revokeObjectURL(entry.url); return false;
      });
      for (const row of rows) {
        if (removedIds.has(row.id) || acceptedIds.has(row.id)) continue;
        let entry = selected.find(item => item.id === row.id);
        if (!entry) {
          entry = { id: row.id, generation, file: { name: row.original_filename, size: row.file_size, type: row.mime_type } };
          selected.push(entry);
        }
        if (!entry.controller && !entry.sending) apply(entry, row);
        preview(entry);
        if (entry.status === 'uploading' && !entry.controller && !entry.timer) entry.timer = setTimeout(() => run(entry, 'status'), 1000);
      }
      loaded = true;
      if (error.dataset.source === 'restore') { error.hidden = true; error.textContent = ''; delete error.dataset.source; }
      changed();
    } catch (failure) {
      if (current !== generation || controller.signal.aborted) return;
      error.dataset.source = 'restore';
      error.hidden = false; error.textContent = 'Не удалось восстановить файлы. Повторим загрузку списка.';
      onChange();
    } finally {
      if (current === generation) {
        listing = null;
        if (refreshAgain && !pendingIdentity()) { refreshAgain = false; refresh(); }
      }
    }
  }
  function cleanupError(entry) {
    if (!active || entry.generation !== generation) return;
    error.dataset.source = 'cleanup';
    error.hidden = false; error.textContent = 'Не удалось подтвердить удаление файла.';
    const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Повторить удаление';
    retry.addEventListener('click', () => { error.hidden = true; error.textContent = ''; cleanup(entry); }); error.append(retry);
  }
  async function cleanup(entry) {
    if (!active || entry.cleaning || entry.accepted || entry.generation !== generation) return;
    entry.cleaning = true; const current = generation, controller = new AbortController(); entry.cleanupController = controller;
    try {
      if (!entry.id) entry.id = (await createDraftPreload(entry.file, entry.uploadId, controller.signal)).id;
      if (current !== generation || controller.signal.aborted) return;
      removedIds.add(entry.id);
      await deleteDraftPreload(entry.id, controller.signal);
      if (current === generation && !controller.signal.aborted) retired.delete(entry);
    } catch (failure) {
      if (current !== generation || controller.signal.aborted) return;
      if ([404, 409, 410].includes(failure.status)) retired.delete(entry);
      else if (!controller.signal.aborted) cleanupError(entry);
    } finally { if (entry.cleanupController === controller) { entry.cleaning = false; entry.cleanupController = null; } }
  }
  function stopEntry(entry) {
    clearTimeout(entry.timer); entry.timer = null;
    entry.controller?.abort(); entry.previewController?.abort(); entry.cleanupController?.abort();
  }
  function retire(entry) {
    entry.removed = true; clearTimeout(entry.timer); entry.previewController?.abort();
    if (entry.id) removedIds.add(entry.id);
    if (!entry.creating) entry.controller?.abort();
    if (entry.url) URL.revokeObjectURL(entry.url);
    retired.add(entry); if (!entry.creating) cleanup(entry);
  }
  async function run(entry, action, confirm = false) {
    if (!active || entry.removed || entry.accepted || entry.controller || entry.generation !== generation) return;
    clearTimeout(entry.timer); entry.timer = null;
    entry.status = 'uploading'; entry.creating = action === 'create';
    const current = generation, controller = new AbortController(); entry.controller = controller; changed();
    try {
      const result = await (action === 'create' ? createDraftPreload(entry.file, entry.uploadId, controller.signal)
        : action === 'retry' ? retryDraftPreload(entry.id, confirm, controller.signal) : getDraftPreload(entry.id, controller.signal));
      if (current !== generation || controller.signal.aborted) return;
      apply(entry, result); revision++;
      if (entry.removed) { cleanup(entry); return; }
      if (entry.status === 'uploading') entry.timer = setTimeout(() => run(entry, 'status'), 1000);
    } catch (failure) {
      if (current !== generation || controller.signal.aborted) return;
      if (entry.removed) { cleanup(entry); return; }
      entry.status = 'error'; entry.authError = failure.status === 401;
      if (failure.code === 'attachment_retry_uncertain') entry.uncertain = true;
    } finally {
      if (entry.controller === controller) { entry.controller = null; entry.creating = false; }
      if (current === generation) {
        if (!entry.removed) changed();
        if (refreshAgain || !loaded) refresh();
      }
    }
  }
  input.addEventListener('change', () => {
    if (disabled || !active || !clientId) { input.value = ''; return; }
    const problems = [], added = [], oversized = [];
    for (const file of input.files) {
      if (file.size > 20 * 1024 * 1024) oversized.push({ name: file.name, size: file.size });
      else if (file.type.toLowerCase().includes('sticker') || /\.tgs$/i.test(file.name)) problems.push(`${file.name}: стикеры не поддерживаются.`);
      else {
        const entry = { file, uploadId: crypto.randomUUID(), generation, status: 'uploading', url: images.has(file.type) ? URL.createObjectURL(file) : null };
        selected.push(entry); added.push(entry);
      }
    }
    revision++; input.value = ''; delete error.dataset.source; error.textContent = problems.join('\n'); error.hidden = !problems.length;
    changed(); for (const entry of added) run(entry, 'create');
    for (const file of oversized) sizeNotices.reject(file.name, file.size);
  });
  function pause() {
    sizeNotices.pause();
    active = false; generation++; listing?.abort(); listing = null; refreshAgain = false;
    for (const entry of [...selected, ...retired]) {
      stopEntry(entry);
      entry.controller = entry.previewController = entry.cleanupController = null;
      entry.creating = entry.cleaning = false;
    }
  }
  return {
    ready: () => loaded && selected.every(entry => entry.status === 'ready'),
    preloadIds: () => selected.map(entry => entry.id),
    start(id) {
      if (active && clientId === id) return;
      if (clientId && clientId !== id) this.clear();
      for (const entry of [...selected, ...retired]) entry.generation = generation;
      clientId = id; active = true; loaded = false; sizeNotices.start(id);
      for (const entry of selected) if (!entry.id) run(entry, 'create');
      for (const entry of retired) cleanup(entry);
      refresh();
    },
    refresh, pause,
    reserve(ids) { for (const entry of selected) if (ids.includes(entry.id)) entry.sending = true; changed(); },
    release(ids) { for (const entry of selected) if (ids.includes(entry.id)) entry.sending = false; changed(); },
    appendSent(container, ids) {
      for (const entry of selected.filter(entry => ids.includes(entry.id))) {
        entry.accepted = true; acceptedIds.add(entry.id); stopEntry(entry);
        const { row } = fileRow(entry); row.classList.add('task-sent-file'); container.append(row);
        if (entry.url) sentUrls.add(entry.url);
      }
      selected = selected.filter(entry => !ids.includes(entry.id)); revision++; changed();
    },
    clearSent() { for (const url of sentUrls) URL.revokeObjectURL(url); sentUrls.clear(); },
    setDisabled(value) { disabled = value; input.disabled = value; render(); },
    clear() {
      pause(); sizeNotices.clear(); for (const entry of selected) if (entry.url) URL.revokeObjectURL(entry.url);
      selected = []; retired.clear(); removedIds.clear(); acceptedIds.clear(); clientId = null; loaded = false;
      input.value = ''; error.hidden = true; error.textContent = ''; changed();
    },
  };
}
