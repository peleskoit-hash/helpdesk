(function () {
  const REQUIRED = "custom_fio";
  const EQUAL_WIDTH = "__equal_width__";
  const NAME_MIN_WIDTH = 180;
  const COLUMN_MAX_WIDTH = 440;

  function mount(root, { api, defaults, tableRowHtml }) {
    const byId = (id) => root.querySelector('#' + id);
    const table = byId("managementUsersTable");
    const scroll = table.parentElement;
    const gear = byId("managementUsersColumnsBtn");
    const dialog = byId("managementUsersColumnsDialog");
    const list = byId("managementUsersColumnDraft");
    const add = byId("managementUsersColumnAdd");
    const save = byId("managementUsersColumnsSave");
    const close = byId("managementUsersColumnsClose");
    const equalWidth = byId("managementUsersColumnsEqualWidth");
    const errorText = byId("managementUsersColumnsError");
    const controller = new AbortController();
    const { signal } = controller;

    let available = new Map();
    let items = [];
    let preferences;
    let draft = [];
    let drag = null;
    let saving = false;
    let frame = 0;
    let naturalWidths = [];

    function normalize(keys) {
      const result = [...new Set(keys)].filter((key) => key !== EQUAL_WIDTH && available.has(key));
      if (available.has(REQUIRED) && !result.includes(REQUIRED)) result.unshift(REQUIRED);
      return result;
    }

    function applyWidth() {
      frame = 0;
      if (!naturalWidths.length) return;
      const style = getComputedStyle(scroll);
      const availableWidth = scroll.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const usedWidth = naturalWidths.reduce((total, column) => total + column.width, 0);
      let total = 0;
      [...table.querySelector("colgroup").children].forEach((col, index) => {
        const column = naturalWidths[index];
        const width = preferences?.includes(EQUAL_WIDTH)
          ? availableWidth / naturalWidths.length
          : column.key === REQUIRED
          ? Math.max(NAME_MIN_WIDTH, Math.floor(availableWidth - usedWidth))
          : column.width;
        col.style.width = width + "px";
        total += width;
      });
      table.style.width = Math.max(total, availableWidth) + "px";
    }

    function scheduleWidth() {
      if (!frame) frame = requestAnimationFrame(applyWidth);
    }

    function measure(columns) {
      if (preferences?.includes(EQUAL_WIDTH)) {
        naturalWidths = columns.map(({ key }) => ({ key, width: 0 }));
        cancelAnimationFrame(frame);
        applyWidth();
        return;
      }
      const probe = table.cloneNode(true);
      probe.removeAttribute("id");
      probe.tHead.rows[0].removeAttribute("id");
      probe.tBodies[0].removeAttribute("id");
      probe.querySelector("colgroup").remove();
      probe.classList.add("management-users-width-probe");
      probe.setAttribute("aria-hidden", "true");
      scroll.append(probe);
      try {
        naturalWidths = [...probe.tHead.rows[0].cells].map((cell, index) => ({
          key: columns[index].key,
          width: columns[index].key === REQUIRED ? 0
            : Math.min(COLUMN_MAX_WIDTH, Math.ceil(cell.getBoundingClientRect().width)),
        }));
      } finally {
        probe.remove();
      }
      cancelAnimationFrame(frame);
      applyWidth();
    }

    function paint() {
      const columns = normalize(preferences ?? defaults).map((key) => available.get(key));
      const colgroup = table.querySelector("colgroup");
      colgroup.replaceChildren(...columns.map(() => document.createElement("col")));
      table.tHead.rows[0].replaceChildren(...columns.map(({ key, label }) => {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.dataset.columnKey = key;
        cell.textContent = label;
        cell.title = label;
        return cell;
      }));
      table.tBodies[0].innerHTML = items.map((item) => tableRowHtml(item, columns)).join("");
      measure(columns);
    }

    function showDraft() {
      add.replaceChildren(new Option("Выберите колонку", ""));
      for (const [key, column] of available) {
        if (!draft.includes(key)) add.add(new Option(column.label, key));
      }
      add.disabled = saving || add.options.length === 1;
      list.replaceChildren(...draft.map((key) => {
        const { label } = available.get(key);
        const row = document.createElement("div");
        row.className = "columns-draft-row";
        row.dataset.columnKey = key;
        const handle = document.createElement("button");
        handle.type = "button";
        handle.className = "columns-drag-handle";
        handle.textContent = "☰";
        handle.title = "Перетащить или использовать стрелки вверх и вниз";
        handle.setAttribute("aria-label", "Переместить: " + label);
        const title = document.createElement("span");
        title.className = "columns-draft-title";
        title.textContent = label;
        row.append(handle, title);
        if (key !== REQUIRED) {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "columns-delete-btn";
          remove.textContent = "×";
          remove.setAttribute("aria-label", "Удалить: " + label);
          row.append(remove);
        }
        return row;
      }));
    }

    function move(row, to) {
      const from = draft.indexOf(row.dataset.columnKey);
      if (to < 0 || to >= draft.length || to === from) return;
      draft.splice(to, 0, draft.splice(from, 1)[0]);
      const anchor = list.children[to];
      list.insertBefore(row, from < to ? anchor.nextSibling : anchor);
    }

    function stopDrag() {
      if (!drag) return;
      const { row, pointerId } = drag;
      drag = null;
      row.classList.remove("column-reorder-dragging");
      if (list.hasPointerCapture(pointerId)) list.releasePointerCapture(pointerId);
    }

    gear.addEventListener("click", () => {
      draft = normalize(preferences ?? defaults);
      equalWidth.checked = preferences?.includes(EQUAL_WIDTH) ?? false;
      errorText.hidden = true;
      showDraft();
      dialog.showModal();
    }, { signal });
    add.addEventListener("change", () => {
      if (!add.value) return;
      draft.push(add.value);
      showDraft();
    }, { signal });
    list.addEventListener("click", (event) => {
      if (!event.target.closest(".columns-delete-btn")) return;
      const key = event.target.closest("[data-column-key]").dataset.columnKey;
      draft = draft.filter((item) => item !== key);
      showDraft();
      add.focus();
    }, { signal });
    list.addEventListener("keydown", (event) => {
      const handle = event.target.closest(".columns-drag-handle");
      if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      const row = handle.closest("[data-column-key]");
      move(row, draft.indexOf(row.dataset.columnKey) + (event.key === "ArrowUp" ? -1 : 1));
      handle.focus();
    }, { signal });
    list.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest(".columns-drag-handle");
      if (!handle || event.button !== 0 || drag) return;
      const row = handle.closest("[data-column-key]");
      drag = { row, pointerId: event.pointerId };
      row.classList.add("column-reorder-dragging");
      list.setPointerCapture(event.pointerId);
    }, { signal });
    list.addEventListener("pointermove", (event) => {
      if (drag?.pointerId !== event.pointerId) return;
      const from = draft.indexOf(drag.row.dataset.columnKey);
      let to = from;
      [...list.children].forEach((row, index) => {
        const box = row.getBoundingClientRect();
        const middle = box.top + box.height / 2;
        if (index < from && event.clientY < middle) to = Math.min(to, index);
        if (index > from && event.clientY > middle) to = Math.max(to, index);
      });
      move(drag.row, to);
    }, { signal });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      list.addEventListener(type, (event) => {
        if (drag?.pointerId === event.pointerId) stopDrag();
      }, { signal });
    }
    close.addEventListener("click", () => dialog.close(), { signal });
    dialog.addEventListener("cancel", (event) => {
      if (saving) event.preventDefault();
    }, { signal });
    dialog.addEventListener("close", stopDrag, { signal });
    save.addEventListener("click", async () => {
      saving = save.disabled = close.disabled = add.disabled = equalWidth.disabled = list.inert = true;
      errorText.hidden = true;
      const keys = normalize(draft);
      if (!keys.includes(REQUIRED)) keys.unshift(REQUIRED);
      if (equalWidth.checked) keys.push(EQUAL_WIDTH);
      try {
        const result = await api("/api/v1/users/me/preferences", {
          method: "PATCH",
          body: JSON.stringify({ management_users_columns: keys }),
        });
        if (signal.aborted) return;
        preferences = result.management_users_columns;
        paint();
        dialog.close();
      } catch (error) {
        if (signal.aborted) return;
        errorText.textContent = error.message;
        errorText.hidden = false;
      } finally {
        saving = save.disabled = close.disabled = equalWidth.disabled = list.inert = false;
        add.disabled = add.options.length === 1;
      }
    }, { signal });

    const observer = new ResizeObserver(scheduleWidth);
    observer.observe(scroll);
    document.fonts?.ready.then(() => {
      if (!signal.aborted && available.size) paint();
    });

    function refresh() {
      gear.disabled = !available.size || preferences === undefined;
      table.hidden = !available.size;
      paint();
    }

    return {
      restore(keys) {
        preferences = Array.isArray(keys) ? keys : null;
        refresh();
      },
      render(catalog, rows) {
        available = new Map(catalog.map((column) => [column.key, column]));
        items = rows;
        if (dialog.open) {
          stopDrag();
          draft = normalize(draft);
          showDraft();
        }
        refresh();
      },
      destroy() {
        controller.abort();
        observer.disconnect();
        cancelAnimationFrame(frame);
        stopDrag();
        if (dialog.open) dialog.close();
      },
    };
  }

  window.ManagementUserColumns = { mount };
})();
