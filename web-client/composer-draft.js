import { getComposerDraft, putComposerDraft } from './api.js?v=20261104-web-composer-draft-v1';

// Server versions arbitrate writes; only unsynchronized text is backed up locally.
export function createComposerDraft(input, changed) {
  const prefix = 'esdesk.composer.';
  const nonce = () => crypto.getRandomValues(new Uint32Array(4)).join('-');
  const tab = nonce();
  let owner, row, intent, capture, recovered, key, timer, request;
  let active = false, running = false, read = true, revision = 0, epoch = 0, uncertain;
  const storage = action => { try { return action(localStorage); } catch { return undefined; } };
  const scoped = () => prefix + encodeURIComponent(owner) + ':';
  function remove(recordKey, token) {
    storage(store => { if (JSON.parse(store.getItem(recordKey))?.token === token) store.removeItem(recordKey); });
  }
  function persist() {
    if (!owner || !intent) return;
    const draftId = intent.draftId || row?.id || null;
    const next = scoped() + (draftId || 'pending') + ':' + tab;
    intent = { ...intent, draftId, clientId: owner, token: nonce(), at: Date.now() };
    const stored = storage(store => {
      store.setItem(next, JSON.stringify(intent));
      if (key && key !== next) store.removeItem(key);
      return true;
    });
    if (stored && recovered) { remove(recovered.key, recovered.token); recovered = null; }
    key = next;
    status();
  }
  function clean() {
    if (intent) remove(key, intent.token);
    if (recovered) remove(recovered.key, recovered.token);
    intent = null; recovered = null; key = null;
    status();
  }
  function status() {
    input.dataset.draftState = intent?.error ? 'error' : intent?.hold ? 'conflict' : intent ? 'pending' : row ? 'saved' : 'loading';
    input.title = intent?.hold ? 'Не удалось синхронизировать черновик. Ваш текст сохранён локально; продолжите редактирование для сохранения.'
      : intent ? 'Изменения черновика сохранены локально и ожидают синхронизации.' : '';
  }
  function schedule(delay = 500) {
    clearTimeout(timer);
    if (active) timer = setTimeout(pump, delay);
  }
  function edit() {
    revision++;
    if (!owner) return;
    intent = { kind: 'edit', text: input.value, version: row?.version ?? null, revision };
    persist();
    schedule();
    changed();
  }
  function acknowledged(value) {
    row = value;
    if (capture && capture.clientId === owner) capture.version = value.version;
  }
  function reconcile(value) {
    row = value;
    if (!intent) return;
    if (intent.draftId && intent.draftId !== row.id) {
      // Never apply a pending operation to a replacement draft UUID.
      intent.hold = true;
    } else if (uncertain && row.text === uncertain.text && row.version === uncertain.version + 1) {
      acknowledged(row);
      if (intent.version === uncertain.version) intent.version = row.version;
    }
    uncertain = null;
    if (intent.kind === 'clear') {
      if (row.text === '') clean();
      else if (intent.version === null && row.text === intent.sentText) intent.version = row.version;
      else if (intent.version !== row.version) {
        // A newer remote edit must survive a successful TASK send in this tab.
        const restore = input.value === '' && revision === intent.revision;
        clean();
        if (restore) { input.value = row.text; changed(); }
      }
    } else if (intent.text === row.text) clean();
    else if (!intent.hold) {
      if (intent.version === null) intent.version = row.version;
      else if (intent.version !== row.version) intent.hold = true;
    }
    if (intent) persist();
  }
  async function pump() {
    if (!active || running || (!read && (!intent || intent.hold))) return;
    running = true;
    const current = epoch, before = revision;
    const controller = new AbortController(); request = controller;
    let operation, delay = 500;
    try {
      if (read || !row) {
        const value = await getComposerDraft(controller.signal);
        if (current !== epoch || !active) return;
        read = false;
        const hadIntent = Boolean(intent);
        reconcile(value);
        if (!hadIntent && !intent && revision === before) { input.value = value.text; changed(); }
      }
      if (!intent || intent.hold) return;
      operation = intent;
      const value = await putComposerDraft(operation.text, operation.version, controller.signal);
      if (current !== epoch || !active) return;
      acknowledged(value);
      if (intent === operation || intent?.token === operation.token) clean();
      else if (intent && intent.version === operation.version) { intent.version = value.version; persist(); }
    } catch (error) {
      if (current !== epoch || !active || error.status === 401) return;
      if (operation) uncertain = operation;
      read = true;
      if (error.status === 409) delay = 0;
      else if (error.status >= 400 && error.status < 500) {
        read = false;
        if (intent) { intent.hold = true; intent.error = true; }
      } else delay = 2000;
      // Retain the exact latest input, even if the failed PUT was an older edit.
      if (intent) persist();
    } finally {
      if (current === epoch) {
        running = false; request = null; status();
        if (active && (read || intent && !intent.hold)) schedule(delay);
      }
    }
  }
  function stop(clear = false) {
    active = false; epoch++; clearTimeout(timer); request?.abort(); request = null; running = false;
    if (clear) {
      owner = null; row = null; intent = null; capture = null; recovered = null; key = null;
      input.value = ''; revision++; status();
    }
  }
  function start(clientId) {
    if (active && owner === clientId) return;
    if (owner !== clientId) {
      stop(true); owner = clientId; revision = 0;
      const backups = storage(store => Object.keys(store).filter(name => name.startsWith(scoped())).map(name => {
        try { return { key: name, ...JSON.parse(store.getItem(name)) }; } catch { return null; }
      }).filter(value => value?.clientId === owner && typeof value.text === 'string'
        && ['edit', 'clear'].includes(value.kind)).sort((a, b) => b.at - a.at));
      const backup = backups?.[0];
      if (backup) {
        recovered = { key: backup.key, token: backup.token };
        intent = { ...backup, revision: ++revision };
        input.value = backup.text;
      }
    }
    active = true; read = true; epoch++; status(); changed();
    if (intent) persist();
    schedule(0);
  }
  input.addEventListener('input', edit);
  window.addEventListener('online', () => { if (active) { read = true; schedule(0); } });
  window.addEventListener('pagehide', () => stop());
  return {
    start, stop,
    snapshot() {
      capture = { clientId: owner, draftId: row?.id, text: input.value, revision, version: row?.version ?? null };
      return capture;
    },
    sent(sent) {
      if (!active || !sent || sent.clientId !== owner || revision !== sent.revision || input.value !== sent.text) return;
      input.value = ''; revision++;
      intent = { kind: 'clear', text: '', sentText: sent.text, version: sent.version,
        draftId: sent.draftId || row?.id || null, revision };
      capture = null;
      persist(); read = true; schedule(0); changed();
    },
  };
}
