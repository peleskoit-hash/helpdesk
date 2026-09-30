(function () {
  const API = "/api/v1/management/saved-files";
  const EMPTY = "\u2014";

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[ch]);
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        message = payload?.detail?.error?.message || payload?.detail || message;
      } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }

  function formatSize(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} Б`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} КБ`;
    return `${(value / 1024 / 1024).toFixed(value < 10 * 1024 * 1024 ? 1 : 0).replace(".0", "")} МБ`;
  }

  function usageText(item) {
    const sources = Array.isArray(item?.usage_sources) ? item.usage_sources : [];
    return sources.length ? sources.join(", ") : EMPTY;
  }

  function initManagementSavedFilesPage({ root }) {
    const el = {};
    ["Status", "UploadBtn", "DownloadBtn", "RenameBtn", "DeleteBtn", "ActionNote", "UploadInput",
      "TableBody", "Empty", "RenameModal", "RenameCloseBtn", "RenameForm", "RenameInput",
      "RenameError", "RenameSaveBtn"].forEach((key) => {
      el[key] = root.querySelector("#managementSavedFiles" + key);
    });
    const state = { items: [], selectedId: null, loading: false };
    const listeners = [];

    function listen(target, event, handler) {
      target.addEventListener(event, handler);
      listeners.push(() => target.removeEventListener(event, handler));
    }

    function selectedItem() {
      return state.items.find((item) => item.id === state.selectedId) || null;
    }

    function setStatus(message) {
      el.Status.textContent = message || "";
      el.Status.hidden = !message;
    }

    function setNote(message) {
      el.ActionNote.textContent = message || "";
      el.ActionNote.hidden = !message;
    }

    function updateActions() {
      const item = selectedItem();
      const hasSelection = Boolean(item);
      el.DownloadBtn.disabled = !hasSelection;
      el.RenameBtn.disabled = !hasSelection;
      el.DeleteBtn.disabled = !hasSelection || Boolean(item?.used);
      if (item?.used) {
        setNote(`Файл используется: ${usageText(item)}. Удаление недоступно.`);
      } else if (!state.loading) {
        setNote("");
      }
    }

    function render() {
      el.TableBody.innerHTML = "";
      if (!state.items.length) {
        el.Empty.hidden = false;
        el.Empty.textContent = "Список пуст";
      } else {
        el.Empty.hidden = true;
      }
      for (const item of state.items) {
        const selected = item.id === state.selectedId;
        const tr = document.createElement("tr");
        tr.className = "management-saved-files-row" + (selected ? " is-selected" : "");
        tr.dataset.savedFileId = item.id;
        tr.tabIndex = 0;
        tr.innerHTML = `
          <td>${escapeHtml(item.display_filename)}</td>
          <td>${escapeHtml(formatSize(item.file_size))}</td>
          <td>${item.used ? "Да" : "Нет"}</td>
          <td>${escapeHtml(usageText(item))}</td>
        `;
        el.TableBody.appendChild(tr);
      }
      updateActions();
    }

    async function loadFiles({ keepSelection = true } = {}) {
      state.loading = true;
      setStatus("Загрузка...");
      setNote("");
      el.Empty.hidden = false;
      el.Empty.textContent = "Загрузка...";
      try {
        const payload = await fetchJson(API);
        state.items = Array.isArray(payload.items) ? payload.items : [];
        if (!keepSelection || !state.items.some((item) => item.id === state.selectedId)) {
          state.selectedId = null;
        }
        setStatus("Готово");
        render();
      } catch (error) {
        state.items = [];
        state.selectedId = null;
        el.TableBody.innerHTML = "";
        el.Empty.hidden = false;
        el.Empty.textContent = "Ошибка загрузки";
        setStatus(error.message || "Ошибка загрузки");
        updateActions();
      } finally {
        state.loading = false;
      }
    }

    function selectRow(id) {
      state.selectedId = id;
      render();
    }

    async function uploadSelectedFile(file) {
      if (!file) return;
      const data = new FormData();
      data.append("file", file);
      setStatus("Загрузка файла...");
      try {
        const created = await fetchJson(API, { method: "POST", body: data });
        state.selectedId = created.id;
        await loadFiles({ keepSelection: true });
        setStatus("Файл добавлен");
      } catch (error) {
        setStatus(error.message || "Не удалось добавить файл");
      } finally {
        el.UploadInput.value = "";
      }
    }

    function downloadSelected() {
      const item = selectedItem();
      if (!item) return;
      const link = document.createElement("a");
      link.href = `${API}/${encodeURIComponent(item.id)}/download`;
      link.download = item.display_filename || "";
      link.rel = "noopener";
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }

    function openRenameModal() {
      const item = selectedItem();
      if (!item) return;
      el.RenameInput.value = item.display_filename || "";
      el.RenameError.hidden = true;
      el.RenameModal.classList.remove("hidden");
      el.RenameInput.focus();
      el.RenameInput.select();
    }

    function closeRenameModal() {
      el.RenameModal.classList.add("hidden");
    }

    async function renameSelected(event) {
      event.preventDefault();
      const item = selectedItem();
      if (!item) return;
      const displayFilename = el.RenameInput.value.trim();
      if (!displayFilename) {
        el.RenameError.textContent = "Введите имя файла";
        el.RenameError.hidden = false;
        return;
      }
      el.RenameSaveBtn.disabled = true;
      try {
        const updated = await fetchJson(`${API}/${encodeURIComponent(item.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ display_filename: displayFilename }),
        });
        state.items = state.items.map((value) => value.id === updated.id ? updated : value);
        state.selectedId = updated.id;
        closeRenameModal();
        render();
        setStatus("Файл переименован");
      } catch (error) {
        el.RenameError.textContent = error.message || "Не удалось переименовать файл";
        el.RenameError.hidden = false;
      } finally {
        el.RenameSaveBtn.disabled = false;
      }
    }

    async function deleteSelected() {
      const item = selectedItem();
      if (!item || item.used) {
        updateActions();
        return;
      }
      if (!window.confirm(`Удалить файл "${item.display_filename}"?`)) return;
      setStatus("Удаление...");
      try {
        await fetchJson(`${API}/${encodeURIComponent(item.id)}`, { method: "DELETE" });
        state.items = state.items.filter((value) => value.id !== item.id);
        state.selectedId = null;
        render();
        setStatus("Файл удалён");
      } catch (error) {
        setStatus(error.message || "Не удалось удалить файл");
        await loadFiles({ keepSelection: true });
      }
    }

    listen(el.UploadBtn, "click", () => {
      if (typeof el.UploadInput.showPicker === "function") {
        el.UploadInput.showPicker();
      } else {
        el.UploadInput.click();
      }
    });
    listen(el.UploadInput, "change", () => uploadSelectedFile(el.UploadInput.files?.[0]));
    listen(el.DownloadBtn, "click", downloadSelected);
    listen(el.RenameBtn, "click", openRenameModal);
    listen(el.DeleteBtn, "click", deleteSelected);
    listen(el.RenameCloseBtn, "click", closeRenameModal);
    listen(el.RenameForm, "submit", renameSelected);
    listen(el.RenameModal, "click", (event) => {
      if (event.target === el.RenameModal) closeRenameModal();
    });
    listen(el.TableBody, "click", (event) => {
      const row = event.target.closest("[data-saved-file-id]");
      if (row) selectRow(row.dataset.savedFileId);
    });
    listen(el.TableBody, "keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("[data-saved-file-id]");
      if (!row) return;
      event.preventDefault();
      selectRow(row.dataset.savedFileId);
    });

    window.HelpdeskSidebar?.init?.();
    loadFiles();
    return {
      destroy() {
        listeners.splice(0).forEach((remove) => remove());
      },
    };
  }

  window.HelpdeskManagementSavedFiles = {
    formatSize,
    initManagementSavedFilesPage,
  };
  window.HelpdeskAppShell?.registerPage?.("management-saved-files", initManagementSavedFilesPage);
})();
