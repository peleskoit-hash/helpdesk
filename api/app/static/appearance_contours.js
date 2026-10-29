import { loadContours, notifyContourDeletion, contourImage } from "/static/background_contour_paint.js?v=20261029-appearance-compact-v1";
export { configureContourPaint, paintContour, notifyAppearance } from "/static/background_contour_paint.js?v=20261029-appearance-compact-v1";
const API = "/api/v1/background-contours";

async function request(path, options = {}) {
  const response = await fetch(path, { credentials: "include", cache: "no-store", ...options });
  if (!response.ok) {
    let message;
    try { message = (await response.json())?.detail?.error?.message; } catch { /* Non-JSON proxy errors. */ }
    const error = new Error(message || "Не удалось выполнить запрос к библиотеке контуров.");
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function shortAuthor(name) {
  const full = String(name || '').trim(), parts = full.split(/\s+/);
  if (parts.length > 1 && /^\p{L}/u.test(parts[1])) return `${parts[0]} ${Array.from(parts[1])[0].toLocaleUpperCase('ru-RU')}.`;
  return full || 'Автор не указан';
}

export function initContourLibrary(section, onChange, selectContour) {
  const list = section.querySelector(".contour-list"), status = section.querySelector(".contour-status");
  const retry = section.querySelector(".contour-retry");
  let loading = null, busy = false, renderedItems = '';
  const previews = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      previews.unobserve(entry.target);
      void contourImage(entry.target.dataset.contourId).then(asset => { entry.target.src = asset.data; })
        .catch(() => entry.target.dispatchEvent(new Event('error')));
    }
  }, { root: section.querySelector('.pattern-library'), rootMargin: '80px' });
  section.addEventListener('contour-destroy', () => previews.disconnect(), { once: true });
  section.addEventListener('contour-catalogue', event => {
    if (section.open && !busy && !loading) {
      if (JSON.stringify(event.detail) !== renderedItems || list.querySelector('.contour-image-missing')) render(event.detail);
      status.textContent = event.detail.length ? '' : 'Пользовательских контуров пока нет.';
      retry.hidden = true;
      onChange();
    }
  });

  function setBusy(value) {
    busy = value;
    list.setAttribute("aria-busy", String(value));
    list.querySelectorAll("[data-contour-delete]").forEach(button => {
      button.disabled = value || button.dataset.allowed !== "true";
    });
  }

  function render(items) {
    renderedItems = JSON.stringify(items);
    previews.disconnect();
    const cards = items.map(item => {
      const card = document.createElement("article");
      card.className = "contour-card";
      card.tabIndex = 0;
      card.dataset.contourId = item.id;
      card.setAttribute("aria-label", item.name);
      card.innerHTML = `<button type="button" class="contour-select" aria-pressed="false"></button><div class="contour-name"></div><div class="contour-body"><img class="contour-preview" alt="" loading="lazy" decoding="async">
        <div class="contour-actions">
          <span class="contour-action-hint" tabindex="0"><button type="button" class="action contour-action" data-contour-delete aria-label="Удалить контур">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></svg>
          </button></span>
        </div></div><div class="contour-footer"><span class="contour-author"></span></div>`;
      const select = card.querySelector(".contour-select");
      select.setAttribute("aria-label", `Выбрать контур «${item.name}»`);
      select.addEventListener("click", () => selectContour(item.id));
      card.addEventListener("keydown", event => {
        if (event.target === card && ['Enter', ' '].includes(event.key) && !select.disabled) {
          event.preventDefault(); selectContour(item.id);
        }
      });
      card.querySelector(".contour-name").textContent = item.name;
      card.querySelector(".contour-name").title = item.name;
      const image = card.querySelector("img");
      image.dataset.contourId = item.id;
      image.addEventListener("error", () => {
        image.hidden = true;
        card.classList.add("contour-image-missing");
        card.title = "Не удалось загрузить превью контура";
        select.disabled = true;
      }, { once: true });
      const author = card.querySelector(".contour-author");
      author.textContent = `Created: ${shortAuthor(item.author_name)}`;
      author.title = item.author_name ? `Created: ${String(item.author_name).trim()}` : author.textContent;
      author.setAttribute('aria-label', author.title);
      const deleteHint = card.querySelector(".contour-action-hint");
      deleteHint.title = item.can_delete === true ? "Удалить контур" : "Удалить контур может только автор или администратор";
      deleteHint.setAttribute("aria-label", deleteHint.title);
      const remove = card.querySelector("[data-contour-delete]");
      remove.dataset.allowed = String(item.can_delete === true);
      remove.disabled = busy || item.can_delete !== true;
      remove.addEventListener("click", () => void deleteContour(item));
      return card;
    });
    list.replaceChildren(...cards);
    list.querySelectorAll('img').forEach(image => previews.observe(image));
  }

  function load() {
    if (loading) return loading;
    status.textContent = "Загрузка контуров…";
    retry.hidden = true;
    list.setAttribute("aria-busy", "true");
    loading = (async () => {
      try {
        const items = await loadContours(true);
        if (!section.isConnected) return;
        render(items);
        status.textContent = items.length ? "" : "Пользовательских контуров пока нет.";
      } catch (error) {
        if (!section.isConnected) return;
        status.textContent = `Не удалось загрузить контуры. ${error.message}`;
        retry.hidden = false;
      } finally {
        loading = null;
        list.setAttribute("aria-busy", String(busy));
        onChange();
      }
    })();
    return loading;
  }

  async function deleteContour(item) {
    if (busy || item.can_delete !== true) return;
    setBusy(true);
    try {
      if (!window.HelpdeskDialogs) await import("/static/desk_dialogs.js?v=20261027-dialog-prompts-v4");
      const menu = section.closest("#appearanceMenu"), bubble = section.querySelector(".pattern-library");
      const wasOpen = menu.matches(":popover-open"), expanded = section.open;
      const menuScroll = menu.scrollTop, bubbleScroll = bubble.scrollTop;
      let confirmed;
      try {
        confirmed = await window.HelpdeskDialogs.confirm(`Удалить контур «${item.name}» из общей библиотеки?`, {
          title: "Удаление контура", acceptLabel: "Удалить",
        });
      } finally {
        // A modal dialog closes native popovers. Restore this action's context afterwards.
        if (wasOpen && section.isConnected && !menu.matches(":popover-open")) {
          menu.showPopover();
          section.open = expanded;
          onChange();
          menu.scrollTop = menuScroll;
          bubble.scrollTop = bubbleScroll;
        }
      }
      if (!confirmed || !section.isConnected) return;
      await loading;
      try { await request(`${API}/${encodeURIComponent(item.id)}`, { method: "DELETE" }); }
      catch (error) { if (error.status !== 404) throw error; }
      notifyContourDeletion(item.id);
      list.querySelectorAll(".contour-card").forEach(card => { if (card.dataset.contourId === item.id) card.remove(); });
      await load();
    } catch (error) {
      if (section.isConnected) {
        await load();
        status.textContent = `Не удалось удалить контур. ${error.message}`;
      }
    } finally {
      setBusy(false);
      onChange();
    }
  }

  section.addEventListener('contour-created', async () => {
    await loading;
    if (section.isConnected) void load();
  });
  retry.addEventListener("click", () => void load());
  section.addEventListener("toggle", () => { if (section.open && !busy) void load(); });
  if (section.open) void load();
}
