(function initBotButtonsLevelView(global) {
  const MAX_LAYOUT_ROW_BUTTONS = 2;

  function layoutColumns(value, fallback = 1) {
    const number = Number(value || fallback);
    return Number.isInteger(number) && number > 0 ? Math.min(number, MAX_LAYOUT_ROW_BUTTONS) : fallback;
  }

  function nodeClass(ctx, node) {
    if (ctx.state.layoutEditMode) return "bot-buttons-level-item bot-layout-drag-item";
    const disabledClass = node.is_enabled === false ? " bot-button-disabled" : "";
    if (node.kind === "terminal") {
      if (node.removed) return "bot-buttons-level-item bot-button-leaf-removed";
      return node.has_instruction
        ? `bot-buttons-level-item bot-button-leaf-has${disabledClass}`
        : `bot-buttons-level-item bot-button-leaf-empty${disabledClass}`;
    }
    return `bot-buttons-level-item${disabledClass}`;
  }

  function cloneRows(_ctx, rows) {
    return JSON.parse(JSON.stringify(rows || []));
  }

  function currentLevel(ctx) {
    return ctx.state.levelStack[ctx.state.levelStack.length - 1] || null;
  }

  function normalizeDraftRows(rows) {
    const normalized = [];
    for (const row of rows || []) {
      const buttons = Array.isArray(row?.buttons) ? row.buttons.filter(Boolean) : [];
      for (let start = 0; start < buttons.length; start += MAX_LAYOUT_ROW_BUTTONS) {
        const chunk = buttons.slice(start, start + MAX_LAYOUT_ROW_BUTTONS);
        normalized.push({ columns: Math.min(layoutColumns(row?.columns, chunk.length), chunk.length), buttons: chunk });
      }
    }
    return normalized;
  }

  function serializeLayoutRows(rows) {
    const result = [];
    for (const row of rows || []) {
      const keys = (row?.buttons || []).map((node) => node?.technical_key).filter(Boolean);
      if (!keys.length) continue;
      if (keys.length > MAX_LAYOUT_ROW_BUTTONS) return null;
      result.push({
        columns: keys.length,
        button_keys: keys,
      });
    }
    return result;
  }

  function removeDraftButton(draft, rowIndex, nodeIndex) {
    const row = draft[rowIndex];
    const [node] = row.buttons.splice(nodeIndex, 1);
    row.columns = row.buttons.length;
    if (!row.columns) draft.splice(rowIndex, 1);
    return node;
  }

  function moveDraftButton(draft, fromRow, fromIndex, toRow, toIndex = null, targetDraft = draft) {
    const source = draft[fromRow];
    const target = targetDraft[toRow];
    if (!source?.buttons[fromIndex] || !target) return false;
    if (source !== target && target.buttons.length >= MAX_LAYOUT_ROW_BUTTONS) return false;
    let index = Number.isFinite(toIndex) ? toIndex : target.buttons.length;
    if (source === target && index > fromIndex) index -= 1;
    index = Math.max(0, Math.min(index, target.buttons.length));
    if (source === target && index === fromIndex) return false;
    // Keep the row in place when reordering its own buttons.
    const node = source === target ? source.buttons.splice(fromIndex, 1)[0] : removeDraftButton(draft, fromRow, fromIndex);
    target.buttons.splice(index, 0, node);
    target.columns = target.buttons.length;
    return true;
  }

  function isUnchangedNewRow(draft, fromRow, targetDraft, beforeRow) {
    return draft === targetDraft && draft[fromRow].buttons.length === 1
      && (beforeRow === fromRow || beforeRow === fromRow + 1);
  }

  function moveDraftButtonToNewRow(draft, fromRow, fromIndex, beforeRow, targetDraft = draft) {
    if (!draft[fromRow]?.buttons[fromIndex]) return false;
    let index = Number.isInteger(beforeRow) ? Math.max(0, Math.min(beforeRow, targetDraft.length)) : targetDraft.length;
    if (isUnchangedNewRow(draft, fromRow, targetDraft, index)) return false;
    if (draft === targetDraft && draft[fromRow].buttons.length === 1 && fromRow < index) index -= 1;
    const node = removeDraftButton(draft, fromRow, fromIndex);
    targetDraft.splice(index, 0, { columns: 1, buttons: [node] });
    return true;
  }

  function cancelLayoutEdit(state) {
    if (!state) return false;
    state.layoutModalCleanup?.();
    state.layoutModalCleanup = null;
    state.layoutEditMode = false;
    state.layoutDraftLevels = null;
    state.layoutMoves = [];
    state.layoutChanged = new Set();
    state.layoutOpenSubmenus = new Set();
    state.layoutDragNode = null;
    state.layoutError = "";
    return true;
  }

  function bindLayoutModal(ctx, previousScroll, finishDrag) {
    const dialog = ctx.el.content.querySelector(".bot-layout-dialog");
    const scroll = dialog.querySelector(".bot-layout-scroll-area");
    const zones = Array.from(dialog.querySelectorAll("[data-layout-scroll]"));
    let dragging = false, direction = 0, frame = 0, lastTime = 0;
    function updateZones() {
      for (const zone of zones) {
        zone.hidden = !dragging || (Number(zone.dataset.layoutScroll) < 0
          ? scroll.scrollTop <= 1 : scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 1);
        zone.classList.toggle("bot-layout-auto-scroll-active", !zone.hidden && Number(zone.dataset.layoutScroll) === direction);
      }
    }
    function stopScroll() {
      cancelAnimationFrame(frame);
      frame = 0;
      direction = 0;
      lastTime = 0;
      updateZones();
    }
    function tick(time) {
      frame = 0;
      if (!dragging || !direction || !dialog.isConnected) { stopScroll(); return; }
      const before = scroll.scrollTop;
      scroll.scrollTop += direction * Math.min(32, lastTime ? time - lastTime : 16) * .6;
      lastTime = time;
      updateZones();
      if (scroll.scrollTop === before || zones.find(zone => Number(zone.dataset.layoutScroll) === direction)?.hidden) {
        stopScroll();
      } else frame = requestAnimationFrame(tick);
    }
    dialog.addEventListener("dragover", () => {
      if (dragging || !ctx.state.layoutDragNode) return;
      dragging = true;
      updateZones();
    }, true);
    const endDrag = () => {
      dragging = false;
      stopScroll();
    };
    dialog.addEventListener("dragend", endDrag, true);
    dialog.addEventListener("drop", endDrag, true);
    for (const zone of zones) {
      zone.addEventListener("dragover", event => {
        event.preventDefault(); event.stopPropagation();
        if (!dragging) return;
        direction = Number(zone.dataset.layoutScroll);
        updateZones();
        if (!frame) frame = requestAnimationFrame(tick);
      });
      zone.addEventListener("dragleave", stopScroll);
      zone.addEventListener("drop", event => { event.preventDefault(); event.stopPropagation(); });
    }
    scroll.addEventListener("scroll", updateZones);
    // Opening a submenu can change whether there is room to scroll.
    dialog.addEventListener("toggle", updateZones, true);
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      if (!ctx.state.layoutSaving) dialog.querySelector("#cancelLayoutBtn").click();
    });
    const workArea = ctx.el.content.closest("#appContent");
    const resize = () => {
      const { left, right } = workArea.getBoundingClientRect();
      dialog.style.setProperty("--bot-layout-work-left", `${Math.max(0, left) + 12}px`);
      dialog.style.setProperty("--bot-layout-work-right", `${Math.max(0, window.innerWidth - right) + 12}px`);
    };
    resize();
    window.addEventListener("resize", resize);
    const blur = () => { endDrag(); finishDrag(); };
    window.addEventListener("blur", blur);
    ctx.state.layoutModalCleanup = () => {
      endDrag();
      window.removeEventListener("resize", resize);
      window.removeEventListener("blur", blur);
      if (dialog.open) dialog.close();
    };
    dialog.showModal();
    scroll.scrollTop = previousScroll;
  }

  function canMoveNode(node) {
    return node?.kind === "terminal" && ["ticket", "task", "auto"].includes(node.workflow_type)
      && !!node.button_id && typeof node.parent_path === "string" && !node.removed && !node.is_archived;
  }

  function addDraftLevel(state, key, parentPath, rows) {
    const activeRows = rows.map(row => ({ ...row, buttons: row.buttons.filter(node => !node.removed && node.kind !== "removed_folder") }));
    const draft = normalizeDraftRows(activeRows);
    state.layoutDraftLevels.set(key, { rows: draft, parentPath });
    if (activeRows.some(row => row.buttons.length > MAX_LAYOUT_ROW_BUTTONS)) state.layoutChanged.add(key);
    for (const row of draft) {
      for (const node of row.buttons) {
        if (node.kind === "submenu" && node.level_key) {
          addDraftLevel(state, node.level_key, node.full_path, node.children_rows || []);
        }
      }
    }
  }

  function refreshLevelStack(ctx, structure) {
    ctx.state.menuStructure = structure;
    const stack = ctx.state.levelStack || [];
    const refreshed = [];
    for (let index = 0; index < stack.length; index += 1) {
      const level = stack[index];
      const rows = index === 0 ? structure.tree_rows : findRowsForLevelKey(structure.tree_rows, level.levelKey);
      if (!rows) break;
      refreshed.push({ ...level, rows });
    }
    ctx.state.levelStack = refreshed;
    ctx.state.preview = { kind: null, label: "", technicalKey: "", item: null, loading: false, menuName: ctx.state.selectedMenu };
    ctx.state.buttonColorStructure = null;
    ctx.state.buttonColorRequests = new Map();
  }

  function isLayoutEditableLevel(ctx, level) {
    if (!level) return false;
    if (ctx.state.view !== "level") return false;
    if (!ctx.state.selectedMenu) return false;
    if (!level.levelKey) return false;
    return !String(level.levelKey).includes("|level|__removed__");
  }

  function buildArchiveRowsFromStructure(ctx) {
    const removedItems = Array.isArray(ctx.state.menuStructure?.removed_items) ? ctx.state.menuStructure.removed_items : [];
    return removedItems.map((leaf) => ({
      buttons: [{
        label: leaf.label,
        kind: "terminal",
        technical_key: leaf.technical_key,
        workflow_type: leaf.workflow_type,
        full_path: leaf.full_path,
        has_instruction: !!leaf.has_instruction,
        removed: true,
        is_enabled: true,
        is_archived: !!leaf.is_archived,
        children_rows: [],
      }],
    }));
  }

  function findRowsForLevelKey(rows, levelKey) {
    for (const row of rows || []) {
      for (const node of row?.buttons || []) {
        if (String(node?.level_key || "") === String(levelKey || "")) {
          return Array.isArray(node.children_rows) ? node.children_rows : [];
        }
        const found = findRowsForLevelKey(node.children_rows || [], levelKey);
        if (found !== null) return found;
      }
    }
    return null;
  }

  function applyButtonColors(ctx) {
    const menu = ctx.state.selectedMenu;
    if (ctx.state.buttonColorStructure !== ctx.state.menuStructure) {
      ctx.state.buttonColorStructure = ctx.state.menuStructure;
      ctx.state.buttonColorRequests = new Map();
    }
    const requests = ctx.state.buttonColorRequests ||= new Map();
    ctx.el.content.querySelectorAll("[data-button-color-key]").forEach(button => {
      const key = button.dataset.buttonColorKey;
      const cacheKey = `${menu}|${key}`;
      const apply = item => {
        if (!button.isConnected) return;
        for (const style of ["primary", "success", "danger"]) {
          button.classList.toggle(`bot-button-color-${style}`, item?.button_style === style);
        }
      };
      if (!requests.has(cacheKey)) {
        requests.set(cacheKey, ctx.api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(key)}&menu_type=${encodeURIComponent(menu)}`)
          .catch(() => { requests.delete(cacheKey); return null; }));
      }
      requests.get(cacheKey).then(apply);
    });
  }

  function buttonTypeLabel(node) {
    if (node.kind === "submenu") return "Подменю";
    if (node.kind === "group_task_parent") return "Групповая задача";
    return ({ticket:"Заявка", task:"Задача", auto:"Автоматическая задача", group_task:"Групповая задача"})[node.workflow_type] || "Задача";
  }

  function renderSubmenuContents(ctx, node) {
    const level = ctx.state.layoutDraftLevels.get(node.level_key);
    if (!level) return "";
    const count = level.rows.reduce((count, row) => count + row.buttons.length, 0);
    const key = ctx.escapeHtml(node.technical_key);
    return `<details class="bot-structure-submenu" data-submenu-key="${key}" ${ctx.state.layoutOpenSubmenus.has(node.technical_key) ? "open" : ""}>
      <summary>Содержимое подменю (${count})</summary>
      ${renderLevelRows(ctx, level.rows, node.level_key)}
    </details>`;
  }

  function renderLevelRows(ctx, rows, levelKey) {
    const editing = ctx.state.layoutEditMode;
    const newRow = index => `<div class="bot-layout-new-row-drop" data-layout-new-row-before="${index}" hidden>Новый ряд здесь</div>`;
    return `<div class="bot-buttons-level-list${ctx.state.messengerView && !editing ? " bot-buttons-messenger-view" : ""}"
      ${editing ? `data-layout-level="${ctx.escapeHtml(levelKey)}"` : ""}>
      ${rows.length ? rows.map((row, rowIndex) => `
        ${editing ? newRow(rowIndex) : ""}
        <div class="bot-buttons-row${editing ? " bot-layout-row-edit" : ""}" ${editing ? `data-layout-drop-row="${rowIndex}"` : ""}>
          ${row.buttons.map((node, nodeIndex) => `
            <div class="bot-structure-item">
              <button type="button"
                class="${nodeClass(ctx, node)}${node.kind === "submenu" ? " bot-button-submenu" : ""}"
                data-button-color-key="${ctx.escapeHtml(node.technical_key || "")}"
                data-row-index="${rowIndex}" data-node-index="${nodeIndex}"
                ${editing ? `draggable="${!ctx.state.layoutSaving}" data-layout-drag-button="${rowIndex}:${nodeIndex}"` : ""}>
                <span>${ctx.escapeHtml(node.label)}</span><span class="bot-button-type-tag">${buttonTypeLabel(node)}</span>
              </button>
              ${editing && node.kind === "submenu" ? renderSubmenuContents(ctx, node) : ""}
            </div>`).join("")}
        </div>`).join("") : `<div class="status">Пусто</div>`}
      ${editing ? newRow(rows.length) : ""}
    </div>`;
  }

  function renderLevel(ctx) {
    if (ctx.state.layoutDragNode) return;
    const previousModal = ctx.el.content.querySelector(".bot-layout-dialog");
    const previousScroll = previousModal?.querySelector(".bot-layout-scroll-area")?.scrollTop || 0;
    ctx.state.layoutModalCleanup?.();
    ctx.state.layoutModalCleanup = null;
    if (previousModal && ctx.state.layoutEditMode) {
      ctx.state.layoutOpenSubmenus = new Set(Array.from(previousModal.querySelectorAll(".bot-structure-submenu[open]"), node => node.dataset.submenuKey));
    }
    const current = currentLevel(ctx);
    const isArchiveLevel = String(current?.levelKey || "").includes("|level|__removed__");
    const rows = current?.rows || [];
    const visibleRows = rows
      .filter((row) => !((row?.buttons || [])[0]?.kind === "removed_folder"))
      .map((row) => {
        const buttons = (row?.buttons || []).filter((node) => isArchiveLevel || !node?.removed);
        return { ...row, buttons };
      })
      .filter((row) => (row.buttons || []).length > 0);
    const removedCount = Array.isArray(ctx.state.menuStructure?.removed_items) ? ctx.state.menuStructure.removed_items.length : 0;
    const canEditLayoutBase = isLayoutEditableLevel(ctx, current);
    const isGroupParentLevel = String(current?.kind || "") === "group_task_parent";
    const isSubmenuLevel = String(current?.kind || "") === "submenu";
    const isRootLevel = current?.kind === "root";
    const addButtonLabel = isGroupParentLevel ? "Создать child task" : "Добавить кнопку";
    const canEditLayout = canEditLayoutBase && !isGroupParentLevel;
    const messengerToggle = (isRootLevel || isSubmenuLevel) && !ctx.state.layoutEditMode
      ? global.BotButtonsMenuView.renderMessengerToggle(ctx.state) : "";
    const rootControls = isRootLevel && !ctx.state.layoutEditMode ? `
      <div class="bot-root-controls">
        <section class="bot-menu-management-panel">
          <h2 class="bot-menu-bindings-title">Управление меню</h2>
          <div class="bot-menu-management-actions">
            <button type="button" id="editLayoutBtn" class="action">Изменить структуру</button>
            ${messengerToggle}
            <button type="button" id="addLevelItemBtn" class="action">Добавить новую кнопку</button>
            ${ctx.state.selectedMenu !== "Базовое"
              ? '<button type="button" id="deleteMenuTypeBtn" class="action action-danger">УДАЛИТЬ МЕНЮ ПОЛНОСТЬЮ</button>'
              : ""}
          </div>
        </section>
        ${ctx.renderMenuBindingsPanel()}
      </div>` : "";

    const toolbarHtml = canEditLayout
      ? `
        <div class="bot-layout-toolbar">
          ${ctx.state.layoutEditMode
            ? `<button type="button" id="saveLayoutBtn" class="action action-primary">Сохранить структуру</button>
               <button type="button" id="cancelLayoutBtn" class="action">\u041e\u0442\u043c\u0435\u043d\u0430</button>
               <span class="bot-layout-help">\u041f\u0435\u0440\u0435\u0442\u0430\u0449\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0438, \u0447\u0442\u043e\u0431\u044b \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0440\u044f\u0434\u043e\u043a. \u0412 \u043e\u0434\u043d\u043e\u043c \u0440\u044f\u0434\u0443 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u0434\u043e 2 \u043a\u043d\u043e\u043f\u043e\u043a.</span>`
            : `<button type="button" id="editLayoutBtn" class="action">Изменить структуру</button>
               ${isSubmenuLevel ? `<button type="button" id="editSubmenuBtn" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0434\u043c\u0435\u043d\u044e</button>` : ""}
               <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>${messengerToggle}`}
        </div>
      `
      : `<div class="bot-layout-toolbar">
           ${isGroupParentLevel ? `<button type="button" id="editParentTaskBtn" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043c\u0430\u0442\u0435\u0440\u0438\u043d\u0441\u043a\u0443\u044e \u0437\u0430\u0434\u0430\u0447\u0443</button>` : ""}
           ${isSubmenuLevel ? `<button type="button" id="editSubmenuBtn" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0434\u043c\u0435\u043d\u044e</button>` : ""}
           <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>${messengerToggle}
         </div>`;

    const rootKey = ctx.state.levelStack[0]?.levelKey;
    const rowsForRender = ctx.state.layoutEditMode ? ctx.state.layoutDraftLevels.get(rootKey).rows : visibleRows;

    ctx.el.content.innerHTML = `
      <div class="bot-buttons-split-layout bot-buttons-v2-list">
        <div class="bot-buttons-split-left">
          ${rootControls}
          ${ctx.state.layoutEditMode ? `
          <dialog class="bot-layout-edit-shell bot-layout-dialog" aria-labelledby="botLayoutDialogTitle">
            <header class="bot-layout-dialog-head">
              <h2 id="botLayoutDialogTitle">Режим изменения структуры</h2>
          ` : ""}
          ${rootControls ? "" : toolbarHtml}
          ${ctx.state.layoutEditMode ? `
            </header>
            <div class="bot-layout-scroll-frame">
            <div class="bot-layout-scroll-area">
            ${ctx.state.layoutError ? `<div class="bot-layout-error" role="alert">${ctx.escapeHtml(ctx.state.layoutError)} Можно повторить сохранение или отменить.</div>` : ""}
          ` : ""}
          ${renderLevelRows(ctx, rowsForRender, rootKey)}
          ${ctx.state.layoutEditMode ? `</div>
            <div class="bot-layout-auto-scroll" data-layout-scroll="-1" hidden>▲ Прокрутить вверх</div>
            <div class="bot-layout-auto-scroll" data-layout-scroll="1" hidden>▼ Прокрутить вниз</div>
            </div></dialog>` : ""}
          ${!ctx.state.layoutEditMode && !isArchiveLevel && removedCount > 0 ? `
            <div class="bot-buttons-archive-bottom">
              <button type="button" id="openRemovedArchiveBtn" class="bot-buttons-level-item bot-button-leaf-removed bot-button-archive-main">
                \u0410\u0440\u0445\u0438\u0432 \u0443\u0434\u0430\u043b\u0435\u043d\u043d\u044b\u0445 (${removedCount})
              </button>
            </div>
          ` : ""}
        </div>

      </div>
    `;

    applyButtonColors(ctx);
    document.getElementById("messengerViewBtn")?.addEventListener("click", event => {
      ctx.state.messengerView = !ctx.state.messengerView;
      event.currentTarget.setAttribute("aria-pressed", String(ctx.state.messengerView));
      ctx.el.content.querySelector(".bot-buttons-split-left > .bot-buttons-level-list")?.classList.toggle("bot-buttons-messenger-view", ctx.state.messengerView);
    });
    ctx.el.content.querySelectorAll("[data-row-index][data-node-index]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (ctx.state.layoutEditMode) return;
        if (!await ctx.confirmDiscard()) return;
        ctx.discardEditorChanges();
        const row = rows[Number(btn.dataset.rowIndex)];
        const node = row?.buttons?.[Number(btn.dataset.nodeIndex)];
        if (!node) return;
        ctx.state.preview = ctx.buildPreviewFromNode(node);
        const nextRows = Array.isArray(node.children_rows) ? node.children_rows : [];
        if (node.kind === "submenu" || node.kind === "group_task_parent" || nextRows.length > 0) {
          ctx.state.levelStack.push({
            label: node.label,
            rows: nextRows,
            levelKey: node.level_key || null,
            fullPath: node.full_path || "",
            parentPath: node.parent_path,
            kind: node.kind || "",
            technicalKey: node.technical_key || "",
            workflowType: node.workflow_type || "",
          });
          ctx.render();
          return;
        }
        if (node.kind === "terminal") await ctx.openEditor(node);
      });
    });

    document.getElementById("editLayoutBtn")?.addEventListener("click", () => {
      cancelLayoutEdit(ctx.state);
      ctx.state.messengerView = false;
      ctx.state.layoutEditMode = true;
      const root = ctx.state.levelStack[0];
      ctx.state.layoutDraftLevels = new Map();
      addDraftLevel(ctx.state, root.levelKey, "", root.rows || []);
      ctx.state.layoutOpenSubmenus = new Set(ctx.state.levelStack.slice(1).map(level => level.technicalKey));
      ctx.render();
    });
    document.getElementById("addLevelItemBtn")?.addEventListener("click", async () => {
      await ctx.createLevelItem();
    });
    document.getElementById("editParentTaskBtn")?.addEventListener("click", async () => {
      if (typeof ctx.openParentTaskEditor === "function") await ctx.openParentTaskEditor();
    });
    document.getElementById("editSubmenuBtn")?.addEventListener("click", async () => {
      if (typeof ctx.openSubmenuEditor === "function") await ctx.openSubmenuEditor();
    });
    document.getElementById("cancelLayoutBtn")?.addEventListener("click", () => {
      if (ctx.state.layoutSaving || ctx.state.layoutDragNode) return;
      cancelLayoutEdit(ctx.state);
      ctx.render();
    });
    document.getElementById("saveLayoutBtn")?.addEventListener("click", async () => {
      if (ctx.state.layoutSaving || ctx.state.layoutDragNode) return;
      const layouts = Object.fromEntries([...ctx.state.layoutChanged].map(key => [
        key, serializeLayoutRows(ctx.state.layoutDraftLevels.get(key).rows).map(row => row.button_keys),
      ]));
      if (!ctx.state.layoutChanged.size) {
        cancelLayoutEdit(ctx.state);
        ctx.render();
        return;
      }
      ctx.setStatus("Сохранение структуры...");
      ctx.state.layoutSaving = true;
      ctx.state.layoutError = "";
      ctx.render();
      try {
        const result = await ctx.api("/api/v1/settings/bot-buttons/structure-layout", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ menu_type: ctx.state.selectedMenu, moves: ctx.state.layoutMoves, layouts }),
        });
        cancelLayoutEdit(ctx.state);
        if (result?.structure) {
          refreshLevelStack(ctx, result.structure);
        } else {
          await ctx.refreshMenuStructure();
        }
        ctx.setStatus("\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d");
        global.HelpdeskAppShell?.showSaved?.(ctx.el?.content?.closest(".panel.bot-buttons-panel"));
      } catch (error) {
        ctx.state.layoutError = error.message || "Не удалось сохранить структуру.";
        ctx.setStatus(ctx.state.layoutError);
      } finally {
        ctx.state.layoutSaving = false;
        ctx.render();
      }
    });

    document.getElementById("addDestinationBtn")?.addEventListener("click", async () => {
      const select = document.getElementById("addDestinationSelect");
      const value = String(select?.value || "").trim();
      if (!value || !ctx.state.selectedMenu) return;
      await ctx.api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(ctx.state.selectedMenu)}/destinations`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ destination: value }),
      });
      await ctx.reloadMenuMeta();
      ctx.render();
    });
    document.getElementById("deleteMenuTypeBtn")?.addEventListener("click", async () => {
      if (!ctx.state.selectedMenu) return;
      if (!await window.HelpdeskDialogs.confirm("\u0412\u044b \u0443\u0432\u0435\u0440\u0435\u043d\u044b, \u0447\u0442\u043e \u0445\u043e\u0442\u0438\u0442\u0435 \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043c\u0435\u043d\u044e \u043a\u043d\u043e\u043f\u043e\u043a?")) return;
      if (!await window.HelpdeskDialogs.confirm("\u0412\u044b \u0422\u041e\u0427\u041d\u041e \u0443\u0432\u0435\u0440\u0435\u043d\u044b, \u0447\u0442\u043e \u0445\u043e\u0442\u0438\u0442\u0435 \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043c\u0435\u043d\u044e \u043a\u043d\u043e\u043f\u043e\u043a?")) return;
      ctx.setStatus("\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435 \u043c\u0435\u043d\u044e...");
      await ctx.api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(ctx.state.selectedMenu)}`, { method: "DELETE" });
      await ctx.loadMenuTypes();
      ctx.setStatus("\u041c\u0435\u043d\u044e \u0443\u0434\u0430\u043b\u0435\u043d\u043e");
    });
    ctx.el.content.querySelectorAll("[data-remove-destination]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const value = btn.dataset.removeDestination || "";
        if (!value || !ctx.state.selectedMenu) return;
        await ctx.api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(ctx.state.selectedMenu)}/destinations/${encodeURIComponent(value)}`, {
          method: "DELETE",
        });
        await ctx.reloadMenuMeta();
        ctx.render();
      });
    });

    if (ctx.state.layoutEditMode) {
      const finishDrag = bindLayoutEditing(ctx);
      bindLayoutModal(ctx, previousScroll, finishDrag);
      for (const button of ctx.el.content.querySelectorAll("#saveLayoutBtn, #cancelLayoutBtn")) {
        button.disabled = !!ctx.state.layoutSaving;
      }
    }

    if (previousModal && !ctx.state.layoutEditMode) document.getElementById("editLayoutBtn")?.focus();

    document.getElementById("openRemovedArchiveBtn")?.addEventListener("click", () => {
      const rowsForArchive = buildArchiveRowsFromStructure(ctx);
      ctx.state.levelStack.push({ label: "\u0410\u0440\u0445\u0438\u0432 \u0443\u0434\u0430\u043b\u0435\u043d\u043d\u044b\u0445", rows: rowsForArchive, levelKey: `${ctx.state.selectedMenu}|level|__removed__` });
      ctx.render();
    });

    document.getElementById("renameSectionBtn")?.addEventListener("click", async () => {
      const currentPreview = ctx.state.preview || {};
      const initial = (currentPreview.item?.button_label_effective || currentPreview.label || "").trim();
      const next = await window.HelpdeskDialogs.prompt("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0432 \u0431\u043e\u0442\u0435", initial);
      if (next === null) return;
      await ctx.saveLabelOverrideForNode(currentPreview, next, true);
    });
    document.getElementById("resetSectionLabelBtn")?.addEventListener("click", async () => {
      const currentPreview = ctx.state.preview || {};
      if (!await window.HelpdeskDialogs.confirm("\u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u043f\u0435\u0440\u0435\u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u043d\u043e\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0438 \u0432\u0435\u0440\u043d\u0443\u0442\u044c \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0438\u0437 \u0411\u0414?")) return;
      await ctx.saveLabelOverrideForNode(currentPreview, "", false);
    });
  }

  function bindLayoutEditing(ctx) {
    const dialog = ctx.el.content.querySelector(".bot-layout-dialog");
    const buttons = dialog.querySelectorAll("[data-layout-drag-button]");
    const zones = dialog.querySelectorAll("[data-layout-new-row-before]");
    let source = null;
    let changed = false;
    let zonesShown = false;
    const levelKey = element => element.closest("[data-layout-level]").dataset.layoutLevel;
    const level = element => ctx.state.layoutDraftLevels.get(levelKey(element));
    const crossLevelAllowed = element => level(element) === source.level || canMoveNode(source.node);

    function hideFeedback() {
      for (const zone of zones) zone.hidden = true;
      dialog.querySelectorAll(".bot-layout-drop-active, .bot-layout-drop-forbidden").forEach(element => {
        element.classList.remove("bot-layout-drop-active", "bot-layout-drop-forbidden");
      });
    }

    // The browser still needs the original drag source until dragend.
    function finishDrag() {
      if (!source) return;
      source.button.classList.remove("bot-layout-dragging");
      source = null;
      zonesShown = false;
      ctx.state.layoutDragNode = null;
      hideFeedback();
      if (changed) ctx.render();
    }

    for (const button of buttons) {
      button.addEventListener("dragstart", event => {
        if (ctx.state.layoutSaving) { event.preventDefault(); return; }
        const row = Number(button.dataset.rowIndex), index = Number(button.dataset.nodeIndex);
        const from = level(button);
        const node = from.rows[row].buttons[index];
        source = { button, level: from, row, index, node };
        ctx.state.layoutDragNode = node;
        event.dataTransfer?.setData("text/plain", node.technical_key || "");
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
        button.classList.add("bot-layout-dragging");
      });
      button.addEventListener("dragend", finishDrag);
    }

    // Wait for native drag capture before inserting space that moves the source.
    dialog.addEventListener("dragover", () => {
      if (!source || zonesShown) return;
      zonesShown = true;
      const { level: from, row } = source;
      for (const zone of zones) {
        zone.hidden = !crossLevelAllowed(zone)
          || isUnchangedNewRow(from.rows, row, level(zone).rows, Number(zone.dataset.layoutNewRowBefore));
      }
    }, true);

    for (const target of [...buttons, ...zones, ...dialog.querySelectorAll("[data-layout-drop-row]")]) {
      const before = target.dataset.layoutNewRowBefore;
      const row = Number(target.dataset.layoutDropRow ?? target.dataset.rowIndex);
      const allowed = () => {
        if (!source || changed || !crossLevelAllowed(target)) return false;
        if (before !== undefined) return !target.hidden;
        return target !== source.button && (level(target) === source.level && row === source.row
          || level(target).rows[row].buttons.length < MAX_LAYOUT_ROW_BUTTONS);
      };
      target.addEventListener("dragover", event => {
        event.stopPropagation();
        const valid = allowed();
        if (valid) event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = valid ? "move" : "none";
        target.classList.toggle("bot-layout-drop-active", valid);
        target.classList.toggle("bot-layout-drop-forbidden", !!source && !valid);
      });
      target.addEventListener("dragleave", () => target.classList.remove("bot-layout-drop-active", "bot-layout-drop-forbidden"));
      target.addEventListener("drop", event => {
        event.preventDefault();
        event.stopPropagation();
        if (allowed()) {
          const destination = level(target);
          const sameRow = destination === source.level && row === source.row;
          const targetIndex = Number(target.dataset.nodeIndex);
          const insert = Number.isInteger(targetIndex) ? targetIndex + Number(sameRow && targetIndex > source.index) : null;
          changed = before !== undefined
            ? moveDraftButtonToNewRow(source.level.rows, source.row, source.index, Number(before), destination.rows)
            : moveDraftButton(source.level.rows, source.row, source.index, row, insert, destination.rows);
          if (changed) {
            ctx.state.layoutChanged.add(levelKey(source.button));
            ctx.state.layoutChanged.add(levelKey(target));
            if (destination !== source.level) {
              ctx.state.layoutMoves.push({ button_id: source.node.button_id, parent_path: destination.parentPath });
            }
            ctx.state.layoutError = "";
          }
        }
        hideFeedback();
      });
    }
    for (const submenu of dialog.querySelectorAll(".bot-structure-submenu")) {
      submenu.addEventListener("dragover", event => event.stopPropagation());
      submenu.addEventListener("drop", event => { event.stopPropagation(); hideFeedback(); });
    }
    dialog.addEventListener("drop", hideFeedback);
    return finishDrag;
  }

  global.BotButtonsLevelView = {
    MAX_LAYOUT_ROW_BUTTONS,
    nodeClass,
    cloneRows,
    currentLevel,
    refreshLevelStack,
    isLayoutEditableLevel,
    renderLevel,
    buildArchiveRowsFromStructure,
    normalizeDraftRows,
    serializeLayoutRows,
    moveDraftButton,
    moveDraftButtonToNewRow,
    cancelLayoutEdit,
  };
})(window);
