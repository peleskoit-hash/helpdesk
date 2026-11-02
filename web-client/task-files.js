import { createTaskPreload, getTaskPreload, retryTaskPreload, deleteTaskPreload } from './api.js?v=20261102-web-preloads-v1';

// One selection owns one stable upload ID; the server owns archiving and cleanup.
export function createTaskFiles(input, list, error, onChange = () => {}) {
  let selected = [], disabled = true, taskId = null;
  const retired = new Set(), sentUrls = new Set();
  const images = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
  function fileRow(entry) {
    const row = document.createElement('div');
    row.className = 'task-selected-file';
    row.dataset.status = entry.status;
    const preview = document.createElement(entry.url ? 'img' : 'span');
    if (entry.url) { preview.src = entry.url; preview.alt = entry.file.name; }
    else { preview.className = 'task-file-icon'; preview.textContent = '📎'; preview.setAttribute('aria-hidden', 'true'); }
    const details = document.createElement('div');
    details.className = 'task-file-details';
    const label = document.createElement('span');
    const size = entry.file.size < 1024 ? `${entry.file.size} Б` : entry.file.size < 1024 ** 2
      ? `${(entry.file.size / 1024).toFixed(1)} КБ` : `${(entry.file.size / 1024 ** 2).toFixed(1)} МБ`;
    label.textContent = `${entry.file.name} · ${size}`;
    details.append(label);
    row.append(preview, details);
    return { row, details };
  }
  function button(row, text, action) {
    const control = document.createElement('button');
    control.type = 'button';
    control.textContent = text;
    control.disabled = disabled;
    control.addEventListener('click', () => { if (!disabled) action(); });
    row.append(control);
    return control;
  }
  function render() {
    list.replaceChildren();
    for (const entry of selected) {
      const { row, details } = fileRow(entry);
      const actions = document.createElement('div');
      actions.className = 'task-file-actions';
      if (entry.status === 'uploading') {
        const status = document.createElement('div');
        status.className = 'task-file-loading';
        status.textContent = 'Идёт загрузка...';
        const bar = document.createElement('div');
        bar.className = 'task-file-progress';
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-label', 'Идёт загрузка...');
        details.append(status, bar);
      } else if (entry.status === 'error') {
        const status = document.createElement('div');
        status.className = 'task-file-failure';
        status.setAttribute('role', 'alert');
        status.textContent = entry.uncertain
          ? 'Результат загрузки неизвестен. Повтор может создать лишнюю копию файла.'
          : entry.authError ? 'Сессия истекла. Войдите снова.' : 'Не удалось подготовить файл. Повторите попытку или удалите его.';
        details.append(status);
        button(actions, entry.uncertain ? 'Подтвердить повтор загрузки' : 'Повторить', () => run(entry, entry.id ? 'retry' : 'create', entry.uncertain));
      }
      const remove = button(actions, 'Удалить', () => {
        selected = selected.filter(item => item !== entry);
        retire(entry);
        changed();
      });
      remove.setAttribute('aria-label', `Удалить файл: ${entry.file.name}`);
      row.append(actions);
      list.append(row);
    }
    list.hidden = !selected.length;
  }
  function changed() { render(); onChange(); }
  function cleanupError(entry) {
    if (entry.taskId !== taskId || entry.serverCleanup) return;
    error.hidden = false;
    error.textContent = 'Не удалось подтвердить удаление файла.';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Повторить удаление';
    retry.addEventListener('click', () => { error.hidden = true; error.textContent = ''; cleanup(entry); });
    error.append(retry);
  }
  async function cleanup(entry) {
    if (entry.cleaning || entry.serverCleanup || entry.accepted) return;
    entry.cleaning = true;
    const controller = new AbortController();
    entry.cleanupController = controller;
    try {
      // A lost create response is resolved using the SAME upload ID before DELETE.
      if (!entry.id) entry.id = (await createTaskPreload(entry.taskId, entry.file, entry.uploadId, controller.signal)).id;
      if (!entry.serverCleanup) await deleteTaskPreload(entry.taskId, entry.id, controller.signal);
      retired.delete(entry);
    } catch (failure) {
      if ([404, 409, 410].includes(failure.status)) retired.delete(entry); // Task/file already retired by the server.
      else cleanupError(entry);
    } finally { entry.cleaning = false; entry.cleanupController = null; }
  }
  function retire(entry, serverCleanup = false) {
    entry.removed = true;
    entry.serverCleanup = serverCleanup;
    clearTimeout(entry.timer);
    // Keep an in-flight create response so its newly assigned ID can be deleted.
    if (!entry.creating || serverCleanup) entry.controller?.abort();
    if (entry.url) URL.revokeObjectURL(entry.url);
    retired.add(entry);
    if (serverCleanup) { entry.cleanupController?.abort(); retired.delete(entry); }
    else if (!entry.creating) cleanup(entry);
  }
  async function run(entry, action, confirm = false) {
    if (entry.removed || entry.accepted || entry.controller) return;
    entry.status = 'uploading';
    entry.creating = action === 'create';
    const controller = new AbortController();
    entry.controller = controller;
    changed();
    try {
      const result = await (action === 'create' ? createTaskPreload(entry.taskId, entry.file, entry.uploadId, controller.signal)
        : action === 'retry' ? retryTaskPreload(entry.taskId, entry.id, confirm, controller.signal)
          : getTaskPreload(entry.taskId, entry.id, controller.signal));
      entry.id = result.id;
      if (entry.removed) { cleanup(entry); return; }
      entry.uncertain = result.uncertain === true;
      entry.authError = false;
      entry.status = result.status === 'ready' ? 'ready' : result.status === 'uploading' ? 'uploading' : 'error';
      if (entry.status === 'uploading') entry.timer = setTimeout(() => run(entry, 'status'), 1000);
    } catch (failure) {
      if (entry.removed) { if (!entry.serverCleanup) cleanup(entry); return; }
      entry.status = 'error';
      entry.authError = failure.status === 401;
      if (failure.code === 'attachment_retry_uncertain') entry.uncertain = true;
    } finally {
      entry.controller = null;
      entry.creating = false;
      if (!entry.removed) changed();
    }
  }
  input.addEventListener('change', () => {
    if (disabled || !taskId) { input.value = ''; return; }
    const problems = [], added = [];
    for (const file of input.files) {
      if (file.size > 20 * 1024 * 1024) problems.push(`${file.name}: размер превышает 20 МиБ.`);
      else if (file.type.toLowerCase().includes('sticker') || /\.tgs$/i.test(file.name)) problems.push(`${file.name}: стикеры не поддерживаются.`);
      else {
        const entry = { file, taskId, uploadId: crypto.randomUUID(), status: 'uploading', url: images.has(file.type) ? URL.createObjectURL(file) : null };
        selected.push(entry);
        added.push(entry);
      }
    }
    input.value = '';
    error.textContent = problems.join('\n');
    error.hidden = !problems.length;
    changed();
    for (const entry of added) run(entry, 'create');
  });
  const controls = {
    ready: () => selected.every(entry => entry.status === 'ready'),
    preloadIds: () => selected.map(entry => entry.id),
    setTask(id) { taskId = id; },
    appendSent(container) {
      for (const entry of selected) {
        entry.accepted = true;
        clearTimeout(entry.timer);
        const { row } = fileRow(entry);
        row.classList.add('task-sent-file');
        container.append(row);
        if (entry.url) sentUrls.add(entry.url);
      }
      selected = [];
      changed();
    },
    clearSent() {
      for (const url of sentUrls) URL.revokeObjectURL(url);
      sentUrls.clear();
    },
    setDisabled(value) {
      disabled = value;
      input.disabled = value;
      for (const button of list.querySelectorAll('button')) button.disabled = value;
    },
    clear({ serverCleanup = false } = {}) {
      for (const entry of selected) retire(entry, serverCleanup);
      if (serverCleanup) for (const entry of retired) {
        entry.serverCleanup = true;
        entry.controller?.abort();
        entry.cleanupController?.abort();
        retired.delete(entry);
      }
      selected = [];
      taskId = null;
      input.value = '';
      error.hidden = true;
      error.textContent = '';
      changed();
    },
  };
  return controls;
}
