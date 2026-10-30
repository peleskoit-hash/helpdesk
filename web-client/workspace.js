// Menu is read-only. Chat actions remain local demos with no API or persistence.
import { getWebMenu } from './api.js';
export function mountWorkspace(root) {
  root.innerHTML = `
    <aside class="client-sidebar" aria-label="Панель клиента">
      <div class="client-brand">ES DESK<span>+</span><small>Клиентская поддержка</small></div>
      <div class="notification-anchor">
        <button id="notifications-toggle" aria-expanded="false" aria-controls="notifications-panel">Уведомления по задачам <b class="counter">2</b><span aria-hidden="true">›</span></button>
        <section id="notifications-panel" class="notification-panel" aria-label="Уведомления по задачам" hidden>
          <div class="section-heading"><h2>Уведомления</h2><button id="notifications-close" aria-label="Закрыть уведомления">×</button></div>
          <p class="demo-caption">Демонстрационные события</p>
          <button class="notification-item" data-history="0"><strong>Задача № Д-1042</strong><small>Специалист принял задачу в работу · 10:24</small></button>
          <button class="notification-item" data-history="1"><strong>Заявка № Д-1038</strong><small>Обращение закрыто · вчера</small></button>
        </section>
      </div>
      <section class="bot-panel waiting" aria-labelledby="bot-title">
        <div class="section-heading"><h2 id="bot-title">Помощник ES DESK+</h2><span class="online-dot" aria-hidden="true"></span></div>
        <p id="bot-hint">Выберите, с чем нужна помощь</p>
        <div id="bot-buttons" class="bot-buttons"></div>
      </section>
      <button id="history-open">История заявок и задач <span aria-hidden="true">↗</span></button>
      <section class="client-info" aria-labelledby="client-info-title">
        <h2 id="client-info-title">Информация о клиенте</h2><p class="demo-caption">Демонстрационные данные</p>
        <dl><div><dt>Клиент</dt><dd>Александр Смирнов</dd></div><div><dt>Организация</dt><dd>ООО «Пример»</dd></div><div><dt>Подразделение</dt><dd>Клиентский отдел</dd></div><div><dt>Электронная почта</dt><dd>client@example.com</dd></div></dl>
      </section>
      <div class="client-bottom"><p class="demo-caption">UI-прототип · действия только на экране</p><p class="message" id="logout-error" role="alert" hidden></p><button id="logout-button">Выйти</button></div>
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
        <div class="composer-actions"><button type="button" id="demo-files">＋ Добавить файлы</button><small id="composer-hint" role="status">Текст и файлы никуда не отправляются</small><button type="submit" id="demo-send" class="primary">Отправка <span aria-hidden="true">↑</span></button></div>
      </form>
    </section>
    <dialog id="history-dialog" aria-labelledby="history-title"><div class="section-heading"><h2 id="history-title">История заявок и задач</h2><button data-close-dialog aria-label="Закрыть историю">×</button></div><p class="demo-caption">Демонстрационные данные</p><div class="history-layout"><div id="history-list"></div><section id="history-detail" aria-label="История выбранной записи" aria-live="polite"></section></div></dialog>
    <dialog id="appearanceMenu" class="appearance-menu" aria-labelledby="appearance-title"><div class="section-heading"><h2 id="appearance-title">Оформление</h2><button data-close-dialog aria-label="Закрыть оформление">×</button></div><p class="demo-caption">Настройки действуют до обновления страницы</p><div class="appearance-themes"><button data-appearance-theme="light" aria-pressed="true">Светлая</button><button data-appearance-theme="dark" aria-pressed="false">Тёмная</button></div><div class="appearance-controls"></div><button id="appearance-reset">Сбросить настройки</button></dialog>`;

  const byId = id => root.querySelector(`#${id}`);
  let stage = 'menu';
  let category = '';
  const records = [
    { title: 'Задача № Д-1042', subject: 'Настройка рабочего компьютера', status: 'В работе · сегодня', messages: [['client', 'Вы', 'Нужна помощь с настройкой рабочего компьютера.'], ['system', 'Система', 'Демонстрационная задача Д-1042 создана.'], ['bot', 'Поддержка', 'Специалист принял задачу в работу.']] },
    { title: 'Заявка № Д-1038', subject: 'Восстановление доступа', status: 'Закрыта · вчера', messages: [['client', 'Вы', 'Не получается войти в учётную запись.'], ['bot', 'Поддержка', 'Доступ восстановлен. Проверьте вход.'], ['client', 'Вы', 'Всё работает, спасибо!'], ['system', 'Система', 'Обращение закрыто.']] },
    { title: 'Задача № Д-1031', subject: 'Подключение принтера', status: 'Выполнена · 27 октября', messages: [['client', 'Вы', 'Помогите подключить принтер.'], ['bot', 'Поддержка', 'Принтер настроен и готов к работе.'], ['system', 'Система', 'Задача выполнена.']] },
  ];

  function message(container, kind, author, text) {
    const article = document.createElement('article');
    article.className = `chat-message ${kind}`;
    const header = document.createElement('div');
    header.className = 'message-header';
    const name = document.createElement('strong');
    name.textContent = author;
    const time = document.createElement('span');
    time.textContent = 'Демо';
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
    menuStatus(rows.length ? 'Меню поддержки · только просмотр' : 'В вашем меню пока нет доступных кнопок', rows.length > 0);
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
        button.title = hasChildren ? 'Открыть подменю' : 'Действия кнопки пока недоступны';
        if (!hasChildren) button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => {
          if (hasChildren) {
            renderMenu(item.children, container, [...parents, rows], true);
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
    if (moveFocus) container.querySelector('button')?.focus();
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

  function start() {
    stage = 'description';
    category = 'Демонстрационное описание';
    byId('chat-messages').replaceChildren();
    byId('demo-text').value = '';
    for (const id of ['demo-text', 'demo-send', 'demo-files']) byId(id).disabled = false;
    byId('close-conversation').disabled = false;
    byId('conversation-label').textContent = 'Поможем решить ваш вопрос';
    byId('composer-hint').textContent = 'Текст и файлы никуда не отправляются';
    add('system', 'ES DESK+', 'Демонстрационный чат · сегодня');
    add('bot', 'Помощник ES DESK+', 'Здравствуйте! Я помогу направить ваш вопрос нужному специалисту. Меню слева доступно только для просмотра. Переписка здесь остаётся демонстрационной.');
  }
  byId('new-conversation').addEventListener('click', () => {
    if (stage === 'closed') start();
    else add('system', 'Система', 'Сначала закройте текущее демонстрационное обращение. Переписка остаётся в чате.');
  });
  byId('close-conversation').addEventListener('click', () => {
    stage = 'closed';
    add('system', 'Система', 'Демонстрационное обращение закрыто. Переписка сохранена на экране. Чтобы продолжить, нажмите «Новое обращение».');
    for (const id of ['demo-text', 'demo-send', 'demo-files', 'close-conversation']) byId(id).disabled = true;
    byId('conversation-label').textContent = 'Демо-обращение · закрыто';
    byId('composer-hint').textContent = 'Обращение закрыто · ввод недоступен';
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
    if (!text || stage === 'closed') return;
    add('client', 'Вы', text);
    byId('demo-text').value = '';
    if (stage === 'description') {
      stage = 'active';
      add('system', 'Система', `Демонстрационная задача Д-1043 создана: ${category}.`);
      add('bot', 'Помощник ES DESK+', 'Спасибо! Описание принято в демонстрационном режиме. Специалист подключится к обращению.');
      byId('conversation-label').textContent = 'Демо-обращение Д-1043 · открыто';
    } else add('bot', 'Поддержка ES DESK+', 'Это пример ответа поддержки. Сообщение осталось только в этом макете.');
    byId('composer-hint').textContent = 'Показано локально · ничего не отправлено';
  });
  byId('demo-files').addEventListener('click', () => {
    byId('composer-hint').textContent = 'Демо: файл «Описание проблемы.png» · выбор и загрузка отключены';
  });
  function notifications(open) {
    byId('notifications-panel').hidden = !open;
    byId('notifications-toggle').setAttribute('aria-expanded', String(open));
    if (!open) byId('notifications-toggle').focus();
  }
  byId('notifications-toggle').addEventListener('click', () => notifications(byId('notifications-panel').hidden));
  byId('notifications-close').addEventListener('click', () => notifications(false));
  root.addEventListener('keydown', event => { if (event.key === 'Escape' && !byId('notifications-panel').hidden) notifications(false); });
  function selectHistory(index) {
    const record = records[index];
    const detail = byId('history-detail');
    detail.replaceChildren();
    const title = document.createElement('h3');
    title.textContent = `${record.title} · ${record.subject}`;
    detail.append(title);
    for (const row of record.messages) message(detail, ...row);
    root.querySelectorAll('[data-record]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.record) === index)));
  }
  records.forEach((record, index) => {
    const button = document.createElement('button');
    button.dataset.record = index;
    button.textContent = `${record.title}\n${record.subject}\n${record.status}`;
    button.addEventListener('click', () => selectHistory(index));
    byId('history-list').append(button);
  });
  function openHistory(index = 0) {
    selectHistory(index);
    byId('history-dialog').showModal();
  }
  byId('history-open').addEventListener('click', () => openHistory());
  root.querySelectorAll('[data-history]').forEach(button => button.addEventListener('click', () => {
    notifications(false);
    openHistory(Number(button.dataset.history));
  }));
  byId('appearance-open').addEventListener('click', () => byId('appearanceMenu').showModal());
  root.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  start();
  return {
    loadMenu,
    reset() {
      menuRequest?.abort();
      menuRequest = null;
      root.querySelector('.bot-panel').removeAttribute('aria-busy');
      menuStatus('Меню поддержки');
      notifications(false);
      start();
    },
  };
}
