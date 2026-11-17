(function () {
function initMassNotificationsPage() {
const MAX_FILES = 10;
const editor = window.TelegramTextEditor;
const requests = new AbortController();
const statusLabels = {
  queued: "В очереди", running: "Выполняется", completed: "Завершена", failed: "Ошибка",
  interrupted: "Прервана", pending: "Ожидает обработки", sending: "Отправляется", sent: "Доставлено",
  partial: "Частично доставлено", unknown: "Результат неизвестен", skipped: "Пропущено",
  uploading: "Загружается", ready: "Подготовлено",
};
const state = {
  selectedFiles: [],
  blocks: [],
  rules: [],
  recipientRequest: 0,
  audience: null,
  sending: false,
  accepted: false,
  attempt: null,
  draftRevision: 0,
  browserUpload: false,
  xhr: null,
  operationId: null,
  operation: null,
  journalRequest: null,
  pollTimer: null,
};

async function loadFilterConfig() {
  const config = await api("/api/v1/management/users/client-card-view-config", { cache: "no-store" });
  if (requests.signal.aborted) return;
  state.blocks = config.blocks.filter((block) => block.is_active !== false);
  const fields = state.blocks.flatMap((block) => block.fields.filter((field) => field.field_type === "select"));
  state.rules = (config.rules || [])
    .filter((rule) => rule && rule.is_active !== false)
    .sort((a, b) => (Number(a.sort_order) || 0) - (Number(b.sort_order) || 0)
      || String(a.id || "").localeCompare(String(b.id || "")));
  for (const [key, select] of [["destination", el.filterFieldSelect], ["location_type", el.locationTypeSelect]]) {
    const field = fields.find((field) => field.key === key);
    setOptions(select, field);
    if (key === "location_type" && field) el.locationTypeLabel.textContent = field.label;
  }
  refreshFilters();
}

const el = {
  filterFieldSelect: document.getElementById("filterFieldSelect"),
  dependentFilters: document.getElementById("dependentFilters"),
  locationTypeLabel: document.getElementById("locationTypeLabel"),
  locationTypeSelect: document.getElementById("locationTypeSelect"),
  recipientCount: document.getElementById("recipientCount"),
  broadcastText: document.getElementById("broadcastText"),
  broadcastEditor: document.getElementById("broadcastEditor"),
  broadcastToolbar: document.getElementById("broadcastToolbar"),
  broadcastPreview: document.getElementById("broadcastPreview"),
  newBroadcastBtn: document.getElementById("newBroadcastBtn"),
  broadcastJournal: document.getElementById("broadcastJournal"),
  broadcastJournalSummary: document.getElementById("broadcastJournalSummary"),
  broadcastJournalDetails: document.getElementById("broadcastJournalDetails"),
  sendBroadcastBtn: document.getElementById("sendBroadcastBtn"),
  addBroadcastFilesBtn: document.getElementById("addBroadcastFilesBtn"),
  broadcastFilesInput: document.getElementById("broadcastFilesInput"),
  broadcastFilesCount: document.getElementById("broadcastFilesCount"),
  broadcastFilesList: document.getElementById("broadcastFilesList"),
  resultBox: document.getElementById("resultBox"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
  backToManagementBtn: document.getElementById("backToManagementBtn"),
};

function showError(message) { el.errorText.value = message || "Неизвестная ошибка"; el.errorModal.classList.remove("hidden"); }
function closeError() { el.errorModal.classList.add("hidden"); }
function closeOnBackdropClick(modal, closeHandler) {
  let startedOnBackdrop = false;
  modal.addEventListener("pointerdown", (event) => {
    startedOnBackdrop = event.target === modal;
  });
  modal.addEventListener("click", (event) => {
    if (startedOnBackdrop && event.target === modal) closeHandler();
    startedOnBackdrop = false;
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store", signal: requests.signal, ...options,
    headers: { accept: "application/json", ...(options.headers || {}) },
  });
  const raw = await response.text();
  let payload = null;
  if (raw) {
    try { payload = JSON.parse(raw); } catch { payload = null; }
  }
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || raw || "Ошибка запроса";
    const err = new Error(detail);
    err.status = response.status;
    err.code = payload?.detail?.error?.code;
    throw err;
  }
  return payload;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
}

function updateFileControls() {
  const locked = state.sending || state.accepted;
  el.addBroadcastFilesBtn.disabled = locked;
  el.broadcastFilesInput.disabled = locked;
  for (const button of el.broadcastFilesList.querySelectorAll(".selected-file-remove")) {
    button.disabled = locked;
  }
}

function renderFiles() {
  const count = state.selectedFiles.length;
  el.broadcastFilesCount.textContent = `${count} из ${MAX_FILES}`;
  el.broadcastFilesList.classList.toggle("hidden", count === 0);
  el.broadcastFilesList.replaceChildren(...state.selectedFiles.map((file, index) => {
    const item = document.createElement("div");
    item.className = "selected-file-item";

    const head = document.createElement("div");
    head.className = "selected-file-head";
    const name = document.createElement("div");
    name.className = "selected-file-name";
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "selected-file-remove";
    remove.textContent = "Убрать";
    remove.addEventListener("click", () => {
      if (state.sending || state.accepted) return;
      const index = state.selectedFiles.indexOf(file);
      if (index === -1) return;
      state.selectedFiles.splice(index, 1);
      state.draftRevision++;
      renderFiles();
    });
    head.append(name, remove);

    const foot = document.createElement("div");
    foot.className = "selected-file-foot";
    const size = document.createElement("div");
    size.className = "selected-file-size";
    const staged = state.accepted ? state.operation?.attachments?.[index] : null;
    const status = state.browserUpload ? "uploading" : staged?.status;
    size.textContent = `${formatFileSize(file.size)} · ${statusLabels[status] || "Ожидает отправки"}`;
    foot.append(size);
    if (staged?.error) foot.append(textNode("span", staged.error));
    appendProgress(item, foot, status);

    item.append(head, foot);
    return item;
  }));
  updateFileControls();
}

function setOptions(select, field) {
  select.replaceChildren(new Option("", ""));
  for (const value of field?.options || []) select.add(new Option(value, value));
}

function selectedFilters() {
  return Object.fromEntries(
    [el.filterFieldSelect, el.locationTypeSelect, ...el.dependentFilters.querySelectorAll("select")]
      .filter((select) => select.value !== "")
      .map((select) => [select.name, select.value])
  );
}

function ruleMatches(rule, filters) {
  const current = String(Object.hasOwn(filters, rule.condition_field_key) ? filters[rule.condition_field_key] : "").trim();
  const expected = String(rule.condition_value ?? "").trim();
  switch (rule.condition_operator) {
    case "always": return true;
    case "equals": return current === expected;
    case "not_equals": return current !== expected;
    case "is_empty": return current === "";
    case "is_not_empty": return current !== "";
    default: return false;
  }
}

function visibleDependentFields(filters) {
  const targets = new Set();
  const fields = new Map();
  const blocks = new Map();
  const resolved = new Set();
  for (const rule of state.rules) {
    const matches = ruleMatches(rule, filters);
    for (const action of rule.actions || []) {
      const scope = action.scope || "editor";
      if (scope !== "editor" && scope !== "both") continue;
      if (action.target_type !== "field" && action.target_type !== "block") continue;
      if (action.action !== "show" && action.action !== "hide") continue;
      if (action.target_type === "field") targets.add(action.target_key);
      // The card resolves visibility conflicts independently for each scope.
      const conflictKey = `${scope}:${action.target_type}:${action.target_key}`;
      if (matches && !resolved.has(conflictKey)) {
        const visibility = action.target_type === "field" ? fields : blocks;
        visibility.set(action.target_key, action.action === "show");
        resolved.add(conflictKey);
      }
    }
  }
  return state.blocks
    .filter((block) => blocks.get(block.key) ?? (block.show_in_editor !== false))
    .flatMap((block) => block.fields.filter((field) => field.field_type === "select"
      && field.key !== "destination" && field.key !== "location_type"
      && (targets.has(field.key) || (blocks.get(block.key) === true && field.is_visible_in_editor !== false))
      && (fields.get(field.key) ?? (field.is_visible_in_editor !== false))));
}

function refreshFilters() {
  state.draftRevision++;
  const filters = selectedFilters();
  let fields;
  while (true) {
    fields = visibleDependentFields(filters);
    const stale = Object.keys(filters).filter((key) => key !== "destination" && key !== "location_type"
      && !fields.some((field) => field.key === key));
    if (!stale.length) break;
    // Re-evaluate cascades after removing values of hidden filters.
    for (const key of stale) delete filters[key];
  }
  const existing = [...el.dependentFilters.querySelectorAll("select")];
  if (existing.length !== fields.length || fields.some((field, index) => existing[index].name !== field.key)) {
    el.dependentFilters.replaceChildren(...fields.map((field) => {
      const label = document.createElement("label");
      const select = document.createElement("select");
      select.name = field.key;
      setOptions(select, field);
      select.value = Object.hasOwn(filters, field.key) ? filters[field.key] : "";
      label.append(field.label, select);
      return label;
    }));
  } else {
    for (const select of existing) select.value = filters[select.name] ?? "";
  }
  loadAudience();
}

function textNode(tag, text, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function appendProgress(item, foot, status) {
  if (status !== "uploading") return;
  const progress = textNode("div", "", "selected-file-progress");
  progress.setAttribute("aria-hidden", "true");
  progress.append(textNode("div", "", "selected-file-progress-fill"));
  item.classList.add("selected-file-item-active");
  foot.append(progress);
}

function renderJournal(operation) {
  state.operation = operation;
  el.broadcastJournal.classList.remove("hidden");
  el.broadcastJournalSummary.replaceChildren(
    textNode("p", `Операция ${operation.operation_id} · ${statusLabels[operation.status] || operation.status}`),
    textNode("p", `Обработано ${operation.processed_count} из ${operation.total_count}`),
    textNode("p", [
      `Доставлено: ${operation.sent_count}`, `Ошибки: ${operation.failed_count}`,
      `Частично: ${operation.partial_count}`, `Неизвестно: ${operation.unknown_count}`,
      `Пропущено: ${operation.skipped_count}`,
    ].join(" · ")),
  );
  if (operation.error_message) el.broadcastJournalSummary.append(textNode("p", operation.error_message));

  const details = document.createDocumentFragment();
  if (operation.attachments.length) {
    details.append(textNode("h3", "Вложения"));
    const files = textNode("div", "", "selected-files");
    for (const file of operation.attachments) {
      const row = textNode("div", "", "selected-file-item");
      const head = textNode("div", "", "selected-file-head");
      head.append(textNode("div", file.original_filename, "selected-file-name"));
      const foot = textNode("div", "", "selected-file-foot");
      foot.append(textNode("div", `${formatFileSize(file.file_size)} · ${statusLabels[file.status]}`, "selected-file-size"));
      if (file.error) foot.append(textNode("span", file.error));
      appendProgress(row, foot, file.status);
      row.append(head, foot);
      files.append(row);
    }
    details.append(files);
  }
  if (operation.recipients.length) {
    details.append(textNode("h3", "Получатели"));
    const list = document.createElement("ul");
    for (const recipient of operation.recipients) {
      const row = textNode("li", `${recipient.client_name} · ${statusLabels[recipient.status]}`);
      if (recipient.error) row.append(textNode("div", recipient.error));
      const components = document.createElement("ul");
      for (const component of recipient.components) {
        const label = component.kind === "text" ? "Текст" : `Вложение ${Number(component.key.split(":")[1]) + 1}`;
        const error = component.error ? ` · ${component.error}` : "";
        components.append(textNode("li", `${label} · ${statusLabels[component.status]}${error}`));
      }
      row.append(components);
      list.append(row);
    }
    details.append(list);
  }
  el.broadcastJournalDetails.replaceChildren(details);
  if (state.accepted) renderFiles();
}

const activeOperation = (status) => status === "queued" || status === "running";

function schedulePoll(operationId, delay) {
  clearTimeout(state.pollTimer);
  if (requests.signal.aborted) return;
  state.pollTimer = setTimeout(() => pollJournal(operationId), delay);
}

async function pollJournal(operationId) {
  if (requests.signal.aborted || operationId !== state.operationId) return;
  const controller = new AbortController();
  state.journalRequest = controller;
  try {
    const { operation } = await api(`/api/v1/mass-notifications/operations/${encodeURIComponent(operationId)}`, {
      signal: controller.signal,
    });
    if (controller.signal.aborted) return;
    renderJournal(operation);
    if (activeOperation(operation.status)) schedulePoll(operationId, 2000);
  } catch (error) {
    if (controller.signal.aborted) return;
    if (!el.broadcastJournalSummary.querySelector("[data-poll-error]")) {
      const notice = textNode("p", "Не удалось обновить журнал рассылки");
      notice.dataset.pollError = "";
      el.broadcastJournalSummary.append(notice);
    }
    if (![401, 403, 404].includes(error.status)) schedulePoll(operationId, 2000);
  } finally {
    if (state.journalRequest === controller) state.journalRequest = null;
  }
}

function trackOperation(operationId, operation = null) {
  clearTimeout(state.pollTimer);
  state.journalRequest?.abort();
  state.operationId = operationId;
  state.operation = operation;
  if (operation) renderJournal(operation);
  if (!operation || activeOperation(operation.status)) schedulePoll(operationId, operation ? 2000 : 0);
}

async function restoreJournal() {
  try {
    const { operation } = await api("/api/v1/mass-notifications/operations/latest");
    if (operation && !state.operationId && !requests.signal.aborted) trackOperation(operation.operation_id, operation);
  } catch (error) {
    if (!requests.signal.aborted && !state.operationId && error.status !== 401) {
      el.resultBox.textContent = "Не удалось восстановить журнал рассылки";
    }
  }
}

function updateSendButton() {
  el.sendBroadcastBtn.disabled = state.sending || state.accepted || !state.audience?.count;
  el.newBroadcastBtn.classList.toggle("hidden", !state.accepted && !state.attempt);
  el.newBroadcastBtn.disabled = state.sending;
}

function snapshotFilters() {
  const filters = selectedFilters();
  return JSON.stringify(filters, Object.keys(filters).sort());
}

async function requestAudience(filters) {
  const query = new URLSearchParams({ filters });
  const { count, audience_fingerprint: fingerprint } = await api(`/api/v1/mass-notifications/preview?${query}`);
  if (!Number.isSafeInteger(count) || count < 0 || !/^[0-9a-f]{64}$/.test(fingerprint)) {
    throw new Error("Сервер не подтвердил состав аудитории");
  }
  return { filters, count, fingerprint };
}

function showAudience(audience, pending = false) {
  state.audience = audience;
  el.recipientCount.textContent = pending ? "…" : String(audience?.count ?? "—");
  updateSendButton();
}

async function loadAudience() {
  const filters = snapshotFilters();
  const request = ++state.recipientRequest;
  showAudience(null, true);
  try {
    const result = await requestAudience(filters);
    if (request === state.recipientRequest && !requests.signal.aborted) showAudience(result);
  } catch (error) {
    if (request !== state.recipientRequest || requests.signal.aborted) return;
    showAudience(null);
    if (error.status !== 401) showError(error.message);
  }
}

function sendRequest(form, hasFiles) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open("POST", "/api/v1/mass-notifications/send");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "json";
    const updateUpload = (active) => {
      if (requests.signal.aborted || !hasFiles) return;
      state.browserUpload = active;
      renderFiles();
    };
    xhr.upload.addEventListener("loadstart", () => updateUpload(true));
    xhr.upload.addEventListener("loadend", () => updateUpload(false));
    xhr.addEventListener("load", () => {
      if (xhr.status === 202 && typeof xhr.response?.operation_id === "string" && xhr.response.operation_id) {
        resolve(xhr.response);
      } else {
        const error = new Error(xhr.response?.detail?.error?.message || "Сервер не подтвердил принятие рассылки");
        error.status = xhr.status;
        error.code = xhr.response?.detail?.error?.code;
        reject(error);
      }
    });
    for (const event of ["error", "timeout", "abort"]) xhr.addEventListener(event, () => reject(new Error(event)));
    xhr.send(form);
  });
}

function sameDraft(first, second) {
  return first.text === second.text && first.filters === second.filters
    && first.files.length === second.files.length && first.files.every((file, index) => file === second.files[index]);
}

async function sendBroadcast() {
  if (state.sending || state.accepted || !state.audience?.count) return;
  const shown = state.audience;
  const draft = { filters: snapshotFilters(), text: el.broadcastText.value.trim(), files: [...state.selectedFiles] };
  if (draft.filters !== shown.filters) {
    await loadAudience();
    return;
  }
  if (!draft.text && !draft.files.length) return showError("Введите текст оповещения или добавьте файл");
  const validation = editor.validateTelegramHtml(draft.text);
  if (validation) return showError(validation);
  const revision = state.draftRevision;
  const request = ++state.recipientRequest;
  let posted = false;
  state.sending = true;
  updateSendButton();
  updateFileControls();
  try {
    const current = await requestAudience(draft.filters);
    if (requests.signal.aborted) return;
    const unchanged = () => state.draftRevision === revision && request === state.recipientRequest
      && sameDraft(draft, {
        filters: snapshotFilters(), text: el.broadcastText.value.trim(), files: state.selectedFiles,
      });
    if (request === state.recipientRequest) showAudience(current);
    if (!unchanged()) return showError("Черновик изменился. Подтвердите рассылку заново.");
    if (!current.count || current.count !== shown.count || current.fingerprint !== shown.fingerprint) {
      return showError("Аудитория изменилась. Проверьте количество получателей и подтвердите рассылку заново.");
    }
    if (state.attempt && (!sameDraft(state.attempt, draft) || state.attempt.fingerprint !== current.fingerprint)) {
      return showError("Принятие предыдущей попытки не подтверждено. Восстановите исходный черновик или выберите «Новая рассылка».");
    }
    const audience = draft.filters === "{}" ? "всем допустимым получателям" : "выбранным получателям";
    if (!window.confirm(`Отправить оповещение ${audience} (${current.count})?`) || !unchanged()) return;
    if (!state.attempt) {
      state.attempt = { ...draft, fingerprint: current.fingerprint, key: crypto.randomUUID() };
    }
    const form = new FormData();
    form.append("text", draft.text);
    form.append("text_format", "telegram_html");
    form.append("filters", draft.filters);
    form.append("audience_fingerprint", current.fingerprint);
    form.append("idempotency_key", state.attempt.key);
    for (const file of draft.files) form.append("files", file);
    posted = true;
    lockEditor(true);
    const result = await sendRequest(form, draft.files.length > 0);
    if (requests.signal.aborted) return;
    state.attempt = null;
    state.accepted = true;
    el.resultBox.textContent = `Рассылка принята · ${result.operation_id}`;
    trackOperation(result.operation_id);
    el.broadcastJournal.classList.remove("hidden");
    el.broadcastJournalSummary.replaceChildren(
      textNode("p", `Операция ${result.operation_id} · ${statusLabels[result.status] || "Принята"}`),
    );
    el.broadcastJournalDetails.replaceChildren();
  } catch (error) {
    if (requests.signal.aborted) return;
    if (!posted) {
      if (request !== state.recipientRequest) return;
      showAudience(null);
      showError(error.message);
    } else if (error.status === 409 && error.code === "audience_changed") {
      state.attempt = null;
      await loadAudience();
      showError("Аудитория изменилась. Проверьте количество получателей и подтвердите рассылку заново.");
    } else if (error.status >= 400 && error.status < 500 && error.status !== 408) {
      state.attempt = null;
      showError(error.message);
    } else {
      showError("Не удалось подтвердить принятие рассылки сервером. Повторная отправка может создать дубликаты");
    }
  } finally {
    state.xhr = null;
    state.browserUpload = false;
    state.sending = false;
    if (!requests.signal.aborted) {
      lockEditor(state.accepted);
      renderFiles();
      updateSendButton();
    }
  }
}

function renderTextPreview() {
  el.broadcastPreview.innerHTML = editor.renderTelegramPreviewHtml(el.broadcastText.value);
}

function lockEditor(locked) {
  el.broadcastText.readOnly = locked;
  el.broadcastToolbar.querySelectorAll("button").forEach((button) => { button.disabled = locked; });
  if (locked) editor.closeEmojiPicker();
}

function startNewBroadcast() {
  if (state.sending) return;
  if (state.attempt && !window.confirm("Сервер мог принять предыдущую рассылку. Начать новый черновик?")) return;
  state.attempt = null;
  state.accepted = false;
  state.draftRevision++;
  state.selectedFiles = [];
  el.broadcastFilesInput.value = "";
  el.broadcastText.value = "";
  lockEditor(false);
  el.resultBox.textContent = "";
  renderTextPreview();
  renderFiles();
  updateSendButton();
}

(function init() {
  el.broadcastToolbar.innerHTML = editor.toolbarHtml();
  editor.bindToolbar(el.broadcastEditor, el.broadcastText);
  el.broadcastText.addEventListener("input", () => {
    state.draftRevision++;
    renderTextPreview();
  });
  renderTextPreview();
  el.newBroadcastBtn.addEventListener("click", startNewBroadcast);
  restoreJournal();
  el.filterFieldSelect.addEventListener("change", refreshFilters);
  el.locationTypeSelect.addEventListener("change", refreshFilters);
  el.dependentFilters.addEventListener("change", refreshFilters);

  el.addBroadcastFilesBtn.addEventListener("click", () => el.broadcastFilesInput.click());
  el.broadcastFilesInput.addEventListener("change", () => {
    const files = [...(el.broadcastFilesInput.files || [])];
    el.broadcastFilesInput.value = "";
    if (state.sending || state.accepted) return;
    if (state.selectedFiles.length + files.length > MAX_FILES) {
      showError("Можно добавить не более 10 файлов");
    } else {
      state.selectedFiles.push(...files);
      state.draftRevision++;
    }
    renderFiles();
  });
  el.sendBroadcastBtn.addEventListener("click", sendBroadcast);
  el.backToManagementBtn?.addEventListener("click", () => {
    if (window.HelpdeskAppShell?.goBack) {
      window.HelpdeskAppShell.goBack();
      return;
    }
    window.history.back();
  });

  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  closeOnBackdropClick(el.errorModal, closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try { await navigator.clipboard.writeText(el.errorText.value); }
    catch { document.execCommand("copy"); }
  });
  window.HelpdeskSidebar.init({ onError: (e) => showError(e.message) })
    .then(loadFilterConfig)
    .catch((error) => {
      if (!requests.signal.aborted && error?.status !== 401) showError(error.message);
    });
})();
return () => {
  requests.abort();
  state.journalRequest?.abort();
  clearTimeout(state.pollTimer);
  state.xhr?.abort();
  editor.closeEmojiPicker();
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("mass-notifications", initMassNotificationsPage);
} else {
  initMassNotificationsPage();
}
})();
