// Ordinary tasks use WEB API. The technical chat stays temporary, in memory only.
import { getWebMenu, sendTaskAction, requestTaskResult, getWebHistory, TaskError } from './api.js';
export function mountWorkspace(root) {
  root.innerHTML = `
    <aside class="client-sidebar" aria-label="Панель клиента">
      <div class="client-brand">ES DESK<span>+</span><small>Клиентская поддержка</small></div>
      <button id="notifications-toggle" disabled>Уведомления пока недоступны</button>
      <section class="bot-panel waiting" aria-labelledby="bot-title">
        <div class="section-heading"><h2 id="bot-title">Помощник ES DESK+</h2><span class="online-dot" aria-hidden="true"></span></div>
        <p id="bot-hint">Выберите, с чем нужна помощь</p>
        <div id="bot-buttons" class="bot-buttons"></div>
        <section id="task-controls" hidden><p id="task-feedback" role="status"></p><p id="task-error" role="alert" hidden></p><div class="task-actions"><button id="task-confirm" type="button" class="primary" hidden>Подтвердить</button><button id="task-cancel" type="button" hidden>Отменить создание задачи</button><button id="task-retry" type="button" hidden>Повторить запрос</button><button id="task-sign-in" type="button" hidden>Войти снова</button></div></section>
      </section>
      <button id="history-open">История заявок <span aria-hidden="true">↗</span></button>
      <section class="client-info" aria-labelledby="client-info-title">
        <h2 id="client-info-title">Информация о клиенте</h2><p class="demo-caption">Демонстрационные данные</p>
        <dl><div><dt>Клиент</dt><dd>Александр Смирнов</dd></div><div><dt>Организация</dt><dd>ООО «Пример»</dd></div><div><dt>Подразделение</dt><dd>Клиентский отдел</dd></div><div><dt>Электронная почта</dt><dd>client@example.com</dd></div></dl>
      </section>
      <div class="client-bottom"><p class="demo-caption">Временный WEB-чат · история не сохраняется после обновления</p><p class="message" id="logout-error" role="alert" hidden></p><button id="logout-button">Выйти</button></div>
    </aside>
    <section class="client-chat" aria-label="Чат с поддержкой">
      <header class="chat-toolbar">
        <div><h1 id="session-title" tabindex="-1">Поддержка ES DESK+</h1><p id="conversation-label">Поможем решить ваш вопрос</p></div>
        <div class="chat-actions"><button id="new-conversation" class="primary">Новое обращение</button><button id="close-conversation" class="danger" disabled>Закрыть обращение</button></div>
        <button id="appearance-open">Оформление</button>
      </header>
      <div id="chat-messages" class="chat-messages" role="log" aria-label="Сообщения" aria-live="polite"></div>
      <form id="demo-composer" class="demo-composer">
        <label for="demo-text">Сообщение</label><textarea id="demo-text" rows="3" maxlength="2000" placeholder="Напишите сообщение…"></textarea>
        <div class="composer-options"><label class="composer-enter-toggle" for="sendByEnterCheckbox"><input id="sendByEnterCheckbox" type="checkbox"><span>Отправлять по Enter</span></label><span id="sendHotkeyHint" class="composer-hotkey-hint">Отправка: Ctrl+Enter</span></div>
        <div class="composer-actions"><button type="button" id="demo-files">＋ Добавить файлы</button><small id="composer-hint" role="status">Выберите задачу в меню, чтобы отправить описание</small><button type="submit" id="demo-send" class="primary">Отправка <span aria-hidden="true">↑</span></button></div>
      </form>
    </section>
    <dialog id="history-dialog" data-view="list" aria-labelledby="history-title"><div class="section-heading"><button id="history-back" type="button" aria-label="Назад к списку заявок">← Назад</button><h2 id="history-title">История заявок</h2><button data-close-dialog aria-label="Закрыть историю">×</button></div><div class="history-layout"><div id="history-list" aria-live="polite"></div><section id="history-detail" tabindex="-1" aria-label="История выбранной записи" aria-live="polite"></section></div></dialog>
    <dialog id="appearanceMenu" class="appearance-menu" aria-labelledby="appearance-title"><div class="section-heading"><h2 id="appearance-title">Оформление</h2><button data-close-dialog aria-label="Закрыть оформление">×</button></div><p class="demo-caption">Настройки действуют до обновления страницы</p><div class="appearance-themes"><button data-appearance-theme="light" aria-pressed="true">Светлая</button><button data-appearance-theme="dark" aria-pressed="false">Тёмная</button></div><div class="appearance-controls"></div><button id="appearance-reset">Сбросить настройки</button></dialog>`;

  const byId = id => root.querySelector(`#${id}`);
  let activeTask = null;
  let pendingAction = null;
  let taskBusy = false;
  let taskRequest;
  let generation = 0;
  let records = [];
  let historyPage = 1;
  let selectedResult = null;
  let listRequest;
  let detailRequest;
  const downloads = new Set();

  function message(container, kind, author, text) {
    const article = document.createElement('article');
    article.className = `chat-message ${kind}`;
    const header = document.createElement('div');
    header.className = 'message-header';
    const name = document.createElement('strong');
    name.textContent = author;
    const time = document.createElement('span');
    time.textContent = container.id === 'chat-messages' ? new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : 'Демо';
    header.append(name, time);
    const body = document.createElement('p');
    body.textContent = text;
    article.append(header, body);
    container.append(article);
    container.scrollTop = container.scrollHeight;
  }
  const add = (kind, author, text) => message(byId('chat-messages'), kind, author, text);

  let menuRequest;
  function menuStatus(text, waiting = false) {
    byId('bot-buttons').replaceChildren();
    byId('bot-hint').textContent = text;
    root.querySelector('.bot-panel').classList.toggle('waiting', waiting);
  }
  function renderMenu(rows, container, parents = [], moveFocus = false) {
    menuStatus(rows.length ? 'Выберите тему в меню' : 'В вашем меню пока нет доступных кнопок', rows.length > 0);
    for (const row of rows) {
      const line = document.createElement('div');
      line.className = `menu-row columns-${row.length}`;
      for (const item of row) {
        const group = document.createElement('div');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `bot-button ${{ primary: 'blue', success: 'green', danger: 'red' }[item.style] || 'plain'}`;
        button.textContent = item.label;
        const hasChildren = Array.isArray(item.children) && item.children.length > 0;
        button.title = hasChildren ? 'Открыть подменю' : item.workflow_type === 'task' ? 'Создать обычную задачу' : 'Этот тип кнопки пока недоступен';
        if (!hasChildren && item.workflow_type === 'task') button.dataset.taskTerminal = item.id;
        if (!hasChildren) button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => {
          if (hasChildren) {
            renderMenu(item.children, container, [...parents, rows], true);
          } else if (item.workflow_type === 'task') {
            beginTask(item);
          } else {
            container.querySelectorAll('[aria-pressed]').forEach(other => other.setAttribute('aria-pressed', 'false'));
            button.setAttribute('aria-pressed', 'true');
            byId('bot-hint').textContent = `«${item.label}» · действие пока недоступно`;
          }
        });
        group.append(button);
        line.append(group);
      }
      container.append(line);
    }
    if (parents.length) {
      const back = document.createElement('button');
      back.type = 'button';
      back.className = 'bot-button plain';
      back.textContent = '⬅ Назад';
      back.addEventListener('click', () => renderMenu(parents.at(-1), container, parents.slice(0, -1), true));
      container.append(back);
    }
    updateTaskUI();
    if (moveFocus) container.querySelector('button:not(:disabled)')?.focus();
  }
  async function loadMenu() {
    menuRequest?.abort();
    const controller = new AbortController();
    menuRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    menuStatus('Загружаем меню…');
    root.querySelector('.bot-panel').setAttribute('aria-busy', 'true');
    try {
      const menu = await getWebMenu(controller.signal);
      if (menuRequest !== controller) return;
      renderMenu(menu.rows, byId('bot-buttons'));
    } catch {
      if (menuRequest !== controller) return;
      menuStatus('Не удалось загрузить меню. Попробуйте ещё раз.');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = 'Повторить загрузку меню';
      retry.addEventListener('click', loadMenu);
      byId('bot-buttons').append(retry);
    } finally {
      clearTimeout(timeout);
      if (menuRequest === controller) root.querySelector('.bot-panel').removeAttribute('aria-busy');
    }
  }

  function updateTaskUI() {
    const unresolved = Boolean(pendingAction);
    byId('task-controls').hidden = !activeTask && byId('task-error').hidden;
    byId('task-confirm').hidden = activeTask?.state !== 'confirmation_required';
    byId('task-confirm').disabled = taskBusy || unresolved;
    byId('task-cancel').hidden = !activeTask?.taskId;
    byId('task-cancel').disabled = taskBusy || unresolved;
    byId('task-retry').hidden = taskBusy || !unresolved;
    byId('task-sign-in').hidden = byId('task-error').dataset.status !== '401';
    byId('demo-text').disabled = taskBusy || unresolved || activeTask?.state === 'confirmation_required';
    byId('demo-send').disabled = byId('demo-text').disabled;
    byId('demo-files').disabled = taskBusy || unresolved;
    byId('close-conversation').disabled = true; // Ordinary requests are not connected.
    for (const button of root.querySelectorAll('[data-task-terminal]')) {
      button.disabled = taskBusy || Boolean(activeTask);
      button.setAttribute('aria-pressed', String(activeTask?.buttonId === button.dataset.taskTerminal));
    }
    byId('conversation-label').textContent = 'Технический WEB-чат';
    byId('composer-hint').textContent = taskBusy ? 'Ожидаем ответ сервера…' : unresolved
      ? 'Результат неизвестен. Повторите тот же запрос кнопкой слева.' : activeTask?.state === 'collecting'
        ? 'Отправьте одно текстовое описание задачи' : activeTask?.state === 'confirmation_required'
          ? 'Подтвердите или отмените создание задачи' : 'Чат временный. Для создания задачи выберите кнопку меню.';
  }
  function clearTaskError() {
    byId('task-error').hidden = true;
    byId('task-error').textContent = '';
    delete byId('task-error').dataset.status;
  }
  async function runTaskAction(operation) {
    if (taskBusy) return;
    pendingAction = operation;
    taskBusy = true;
    clearTaskError();
    const current = generation;
    const controller = new AbortController();
    taskRequest = controller;
    byId('task-feedback').textContent = 'Ожидаем ответ сервера…';
    updateTaskUI();
    try {
      const result = await sendTaskAction(operation.path, operation.body, controller.signal);
      if (current !== generation) return;
      pendingAction = null;
      if (operation.kind === 'description' && result.state === 'ready') {
        add('client', 'Вы', operation.body.text);
        byId('demo-text').value = '';
      }
      add('bot', 'ES DESK+', result.text);
      if (['ready', 'cancelled'].includes(result.state)) {
        activeTask = null;
        byId('task-feedback').textContent = '';
      } else {
        activeTask = { ...activeTask, taskId: result.task_id, state: result.state };
        byId('task-feedback').textContent = result.state === 'confirmation_required' ? result.text : 'Ожидаем описание задачи в чате';
      }
    } catch (error) {
      if (current !== generation) return;
      // An auth error on a retry does not tell us whether the earlier attempt committed.
      const uncertain = !(error instanceof TaskError) || error.status >= 500 || [401, 403].includes(error.status);
      byId('task-error').textContent = error instanceof TaskError ? error.message
        : 'Ответ сервера не получен. Повторите запрос: его ключ сохранён до обновления страницы.';
      byId('task-error').hidden = false;
      byId('task-error').dataset.status = String(error.status || 0);
      byId('task-feedback').textContent = uncertain ? 'Результат операции пока неизвестен' : '';
      if (!uncertain) {
        pendingAction = null;
        if ((operation.kind === 'select' && !activeTask?.taskId) || error.status === 410 || error.status === 404
            || ['web_task_cancelled', 'web_task_no_longer_collecting', 'web_task_already_created'].includes(error.code)) activeTask = null;
      }
    } finally {
      if (current === generation) {
        taskBusy = false;
        taskRequest = null;
        updateTaskUI();
        if (activeTask?.state === 'collecting' && !pendingAction) byId('demo-text').focus();
      }
    }
  }
  function beginTask(item) {
    if (taskBusy || activeTask || pendingAction) return;
    activeTask = { buttonId: item.id, requestId: crypto.randomUUID(), taskId: null, state: 'selecting' };
    runTaskAction({ kind: 'select', path: 'select', body: { button_id: activeTask.buttonId, request_id: activeTask.requestId } });
  }
  byId('task-confirm').addEventListener('click', () => {
    if (activeTask?.state !== 'confirmation_required' || taskBusy || pendingAction) return;
    runTaskAction({ kind: 'select', path: 'select', body: { button_id: activeTask.buttonId, request_id: activeTask.requestId, confirmed: true } });
  });
  byId('task-cancel').addEventListener('click', () => {
    if (!activeTask?.taskId || taskBusy || pendingAction) return;
    runTaskAction({ kind: 'cancel', path: activeTask.taskId + '/cancel' });
  });
  byId('task-retry').addEventListener('click', () => { if (pendingAction) runTaskAction(pendingAction); });
  byId('task-sign-in').addEventListener('click', () => root.dispatchEvent(new CustomEvent('web-session-expired')));
  function start() {
    activeTask = null;
    pendingAction = null;
    taskBusy = false;
    clearTaskError();
    byId('chat-messages').replaceChildren();
    byId('demo-text').value = '';
    byId('task-feedback').textContent = '';
    updateTaskUI();
    add('system', 'ES DESK+', 'Временный WEB-чат · история не сохраняется после обновления');
    add('bot', 'ES DESK+', 'Выберите обычную задачу в меню. После выбора отправьте одно описание.');
  }
  byId('new-conversation').addEventListener('click', () => {
    byId('composer-hint').textContent = activeTask ? 'Завершите или отмените создание текущей задачи' : 'Обычные заявки пока недоступны. Выберите кнопку задачи в меню.';
  });
  // Same shortcuts as agent ticket_composer.js; preference stays local to this UI.
  byId('sendByEnterCheckbox').addEventListener('change', () => {
    byId('sendHotkeyHint').textContent = byId('sendByEnterCheckbox').checked
      ? 'Новая строка: Shift+Enter' : 'Отправка: Ctrl+Enter';
  });
  byId('demo-text').addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.isComposing || byId('demo-send').disabled) return;
    if (byId('sendByEnterCheckbox').checked) {
      if (event.shiftKey) return;
      if (event.ctrlKey || (!event.shiftKey && !event.altKey && !event.metaKey)) {
        event.preventDefault();
        byId('demo-composer').requestSubmit(byId('demo-send'));
      }
      return;
    }
    if (event.ctrlKey) {
      event.preventDefault();
      byId('demo-composer').requestSubmit(byId('demo-send'));
    }
  });
  byId('demo-composer').addEventListener('submit', event => {
    event.preventDefault();
    const text = byId('demo-text').value.trim();
    if (!text || taskBusy || pendingAction || activeTask?.state === 'confirmation_required') return;
    if (activeTask?.state === 'collecting') {
      runTaskAction({ kind: 'description', path: activeTask.taskId + '/description', body: { text } });
    } else if (!activeTask) {
      add('client', 'Вы', text);
      byId('demo-text').value = '';
      byId('composer-hint').textContent = 'Сообщение осталось во временном чате и не отправлено сотруднику';
    }
  });
  byId('demo-files').addEventListener('click', () => {
    byId('composer-hint').textContent = 'В этом сценарии поддерживается только текстовое описание';
  });
  function historyButton(container, text, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = text;
    button.addEventListener('click', action);
    container.append(button);
    return button;
  }
  function historyText(container, text, tag = 'p') {
    const element = document.createElement(tag);
    element.textContent = text;
    container.append(element);
    return element;
  }
  function historyError(container, error, text, retry) {
    historyText(container, error.status === 401 ? 'Сессия истекла. Войдите снова.' : text).setAttribute('role', 'alert');
    historyButton(container, 'Повторить', retry);
    if (error.status === 401) historyButton(container, 'Войти снова', () => root.dispatchEvent(new CustomEvent('web-session-expired')));
  }
  const resultPath = id => '/' + encodeURIComponent(id);
  const validSummary = record => typeof record?.task_id === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(record.task_id)
    && typeof record.closed_at === 'string' && Number.isFinite(Date.parse(record.closed_at))
    && (record.read_at === null || (typeof record.read_at === 'string' && Number.isFinite(Date.parse(record.read_at))));
  const historyLabel = record => record.subject || `Заявка № ${record.id}`;
  function renderHistoryList(total) {
    const list = byId('history-list');
    list.replaceChildren();
    const table = document.createElement('table');
    table.className = 'history-table';
    table.setAttribute('aria-label', 'История заявок');
    table.innerHTML = '<colgroup><col class="history-source-column"><col><col class="history-date-column"></colgroup><thead><tr><th scope="col">Источник</th><th scope="col">Тема</th><th scope="col">Дата и время</th></tr></thead>';
    const body = document.createElement('tbody');
    for (const record of records) {
      const row = document.createElement('tr');
      const source = document.createElement('td');
      const icon = document.createElement('img');
      icon.className = 'history-source ' + record.channel;
      icon.src = record.channel === 'web' ? '/assets/web-source.webp' : '/assets/telegram-source.svg';
      icon.alt = record.channel === 'web' ? 'WEB' : 'Telegram';
      icon.width = icon.height = 26;
      source.append(icon);
      const subject = document.createElement('td');
      const button = historyButton(subject, historyLabel(record), () => selectHistory(record));
      button.dataset.record = record.id;
      const date = document.createElement('td');
      const time = historyText(date, new Date(record.created_at).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }), 'time');
      time.dateTime = record.created_at;
      row.append(source, subject, date);
      body.append(row);
    }
    table.append(body);
    list.append(table);
    updateHistoryRows();
    if (!records.length) historyText(list, 'Заявок пока нет.');
    const pages = Math.max(1, Math.ceil(total / 15));
    const pagination = document.createElement('nav');
    pagination.className = 'history-pagination';
    pagination.setAttribute('aria-label', 'Страницы истории');
    historyButton(pagination, 'Назад', () => loadHistory(historyPage - 1)).disabled = historyPage === 1;
    let previous = 0;
    for (const page of [...new Set([1, historyPage - 1, historyPage, historyPage + 1, pages])].filter(page => page >= 1 && page <= pages).sort((a, b) => a - b)) {
      if (previous && page - previous > 1) historyText(pagination, '…', 'span');
      const button = historyButton(pagination, String(page), () => loadHistory(page));
      button.setAttribute('aria-label', `Страница ${page}`);
      if (page === historyPage) {
        button.setAttribute('aria-current', 'page');
        button.disabled = true;
      }
      previous = page;
    }
    historyButton(pagination, 'Вперёд', () => loadHistory(historyPage + 1)).disabled = historyPage === pages;
    list.append(pagination);
  }
  function updateHistoryRows() {
    for (const record of records) {
      const button = root.querySelector(`[data-record="${record.id}"]`);
      if (!button) continue;
      button.title = historyLabel(record) + (record.has_web_result ? ` · ${record.read_at ? 'Прочитана' : 'Не прочитана'}` : '');
      button.dataset.read = record.has_web_result ? String(Boolean(record.read_at)) : '';
      button.setAttribute('aria-pressed', String(selectedResult === record.id));
    }
  }
  async function loadHistory(page = 1) {
    byId('history-dialog').dataset.view = 'list';
    listRequest?.abort();
    detailRequest?.abort();
    for (const controller of downloads) controller.abort();
    const controller = new AbortController();
    listRequest = controller;
    records = [];
    selectedResult = null;
    const detail = byId('history-detail');
    detail.replaceChildren();
    detail.removeAttribute('aria-busy');
    historyText(detail, 'Выберите заявку, чтобы посмотреть доступный ответ.');
    const list = byId('history-list');
    list.replaceChildren();
    list.setAttribute('aria-busy', 'true');
    historyText(list, 'Загружаем историю…');
    try {
      const result = await getWebHistory(page, controller.signal);
      if (controller.signal.aborted) return;
      if (!Array.isArray(result?.items) || result.items.length > 15 || !result.items.every(record =>
          typeof record?.id === 'string' && /^[0-9a-f-]{36}$/i.test(record.id)
          && typeof record.subject === 'string' && ['web', 'telegram'].includes(record.channel)
          && typeof record.created_at === 'string' && Number.isFinite(Date.parse(record.created_at))
          && typeof record.has_web_result === 'boolean'
          && (record.read_at === null || (typeof record.read_at === 'string' && Number.isFinite(Date.parse(record.read_at)))))
          || !Number.isInteger(result.total) || result.total < 0 || result.page !== page || result.page_size !== 15) throw new Error('Invalid history');
      const lastPage = Math.max(1, Math.ceil(result.total / 15));
      if (page > lastPage) { loadHistory(lastPage); return; }
      records = result.items;
      historyPage = page;
      renderHistoryList(result.total);
    } catch (error) {
      if (controller.signal.aborted) return;
      list.replaceChildren();
      historyError(list, error, 'Не удалось загрузить историю заявок.', () => loadHistory(page));
    } finally {
      if (listRequest === controller) {
        listRequest = null;
        list.removeAttribute('aria-busy');
      }
    }
  }
  async function downloadResult(record, file, button, status) {
    const controller = new AbortController();
    downloads.add(controller);
    button.disabled = true;
    status.replaceChildren();
    historyText(status, 'Скачиваем файл…');
    try {
      const blob = await requestTaskResult(resultPath(record.id) + '/files/' + encodeURIComponent(file.id), controller.signal, 'GET', true);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = file.filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.replaceChildren();
    } catch (error) {
      if (controller.signal.aborted) return;
      status.replaceChildren();
      historyError(status, error, 'Не удалось скачать файл.', () => downloadResult(record, file, button, status));
    } finally {
      downloads.delete(controller);
      button.disabled = false;
    }
  }
  async function markResultRead(record, controller, status) {
    status.replaceChildren();
    try {
      const result = await requestTaskResult(resultPath(record.id) + '/read', controller.signal, 'POST');
      if (controller.signal.aborted) return;
      if (result?.task_id !== record.id || typeof result.read_at !== 'string' || !Number.isFinite(Date.parse(result.read_at))) throw new Error('Invalid read result');
      record.read_at = result.read_at;
      updateHistoryRows();
    } catch (error) {
      if (controller.signal.aborted) return;
      historyError(status, error, 'Ответ открыт, но отметить заявку прочитанной не удалось.', () => markResultRead(record, controller, status));
    }
  }
  async function selectHistory(record) {
    detailRequest?.abort();
    for (const controller of downloads) controller.abort();
    const controller = new AbortController();
    detailRequest = controller;
    selectedResult = record.id;
    updateHistoryRows();
    const detail = byId('history-detail');
    detail.replaceChildren();
    detail.scrollTop = 0;
    byId('history-dialog').dataset.view = 'detail';
    if (matchMedia('(max-width: 700px)').matches) detail.focus();
    if (!record.has_web_result) {
      detail.removeAttribute('aria-busy');
      historyText(detail, 'Для этой заявки опубликованный ответ пока недоступен.');
      return;
    }
    detail.setAttribute('aria-busy', 'true');
    historyText(detail, 'Загружаем ответ…');
    try {
      const result = await requestTaskResult(resultPath(record.id), controller.signal);
      if (controller.signal.aborted) return;
      if (!validSummary(result) || result.task_id !== record.id || typeof result.text !== 'string'
          || !Array.isArray(result.files) || !result.files.every(file => typeof file.id === 'string'
            && /^[0-9a-f-]{36}$/i.test(file.id) && typeof file.filename === 'string')) throw new Error('Invalid detail');
      detail.replaceChildren();
      historyText(detail, historyLabel(record), 'h3');
      const answer = historyText(detail, '', 'article');
      answer.className = 'chat-message bot';
      historyText(answer, 'Итоговый ответ сотрудника', 'strong');
      if (result.text) {
        historyText(answer, result.text).className = 'history-message-text';
        const actions = document.createElement('div');
        actions.className = 'history-message-actions';
        const status = document.createElement('span');
        status.setAttribute('role', 'status');
        const copy = historyButton(actions, 'Копировать', async () => {
          copy.disabled = true;
          status.textContent = '';
          try {
            await navigator.clipboard.writeText(result.text);
            status.textContent = 'Скопировано';
          } catch {
            status.textContent = 'Не удалось скопировать. Выделите текст и скопируйте вручную.';
          } finally {
            copy.disabled = false;
          }
        });
        copy.className = 'history-copy';
        copy.setAttribute('aria-label', 'Копировать текст ответа');
        actions.append(status);
        answer.append(actions);
      }
      for (const file of result.files) {
        const row = document.createElement('div');
        const status = document.createElement('div');
        status.setAttribute('role', 'status');
        const button = historyButton(row, `Скачать: ${file.filename}`, () => downloadResult(record, file, button, status));
        row.append(status);
        detail.append(row);
      }
      const readStatus = document.createElement('div');
      detail.append(readStatus);
      detail.removeAttribute('aria-busy');
      // A successful GET alone is not enough: publish the answer in the dialog first.
      if (!result.read_at) await markResultRead(record, controller, readStatus);
      else {
        record.read_at = result.read_at;
        updateHistoryRows();
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      detail.replaceChildren();
      historyError(detail, error, 'Не удалось загрузить ответ по заявке.', () => selectHistory(record));
    } finally {
      if (detailRequest === controller) detail.removeAttribute('aria-busy');
    }
  }
  function clearHistory() {
    byId('history-dialog').dataset.view = 'list';
    listRequest?.abort();
    detailRequest?.abort();
    for (const controller of downloads) controller.abort();
    listRequest = null;
    detailRequest = null;
    records = [];
    historyPage = 1;
    selectedResult = null;
    for (const id of ['history-list', 'history-detail']) {
      byId(id).replaceChildren();
      byId(id).removeAttribute('aria-busy');
    }
  }
  byId('history-back').addEventListener('click', () => {
    detailRequest?.abort();
    for (const controller of downloads) controller.abort();
    const selected = root.querySelector(`[data-record="${selectedResult}"]`);
    selectedResult = null;
    updateHistoryRows();
    byId('history-dialog').dataset.view = 'list';
    const detail = byId('history-detail');
    detail.replaceChildren();
    detail.removeAttribute('aria-busy');
    historyText(detail, 'Выберите заявку, чтобы посмотреть доступный ответ.');
    selected?.focus();
  });
  byId('history-dialog').addEventListener('close', clearHistory);
  byId('history-open').addEventListener('click', () => {
    byId('history-dialog').showModal();
    loadHistory();
  });
  byId('appearance-open').addEventListener('click', () => byId('appearanceMenu').showModal());
  root.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  start();
  return {
    loadMenu,
    reset() {
      generation++;
      taskRequest?.abort();
      taskRequest = null;
      menuRequest?.abort();
      menuRequest = null;
      root.querySelector('.bot-panel').removeAttribute('aria-busy');
      menuStatus('Меню поддержки');
      clearHistory();
      start();
    },
  };
}
