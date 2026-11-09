(function initBotButtonsLevelView(global) {
  const MAX_LAYOUT_ROW_BUTTONS = 3;

  function layoutColumns(value, fallback = 1) {
    const number = Number(value || fallback);
    return [1, 2, 3].includes(number) ? number : fallback;
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
      if (!buttons.length) continue;
      normalized.push({
        columns: Math.max(1, Math.min(MAX_LAYOUT_ROW_BUTTONS, layoutColumns(row?.columns, buttons.length), buttons.length)),
        buttons,
      });
    }
    if (!normalized.length) normalized.push({ columns: 1, buttons: [] });
    return normalized;
  }

  function cleanupDraftRows(draft) {
    for (let idx = draft.length - 1; idx >= 0; idx -= 1) {
      if (!Array.isArray(draft[idx]?.buttons) || draft[idx].buttons.length === 0) draft.splice(idx, 1);
    }
    if (!draft.length) draft.push({ columns: 1, buttons: [] });
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

  function moveDraftButton(draft, fromRow, fromIndex, toRow, toIndex = null) {
    if (!Array.isArray(draft)) return false;
    const source = draft[fromRow];
    const target = draft[toRow];
    const item = source?.buttons?.[fromIndex];
    if (!source || !target || !item) return false;
    source.buttons.splice(fromIndex, 1);
    if (target.buttons.length >= MAX_LAYOUT_ROW_BUTTONS) {
      source.buttons.splice(fromIndex, 0, item);
      return false;
    }
    let insertIndex = Number.isFinite(toIndex) ? Number(toIndex) : target.buttons.length;
    if (source === target && insertIndex > fromIndex) insertIndex -= 1;
    insertIndex = Math.max(0, Math.min(insertIndex, target.buttons.length));
    target.buttons.splice(insertIndex, 0, item);
    source.columns = Math.max(1, Math.min(MAX_LAYOUT_ROW_BUTTONS, source.buttons.length || 1));
    target.columns = Math.max(1, Math.min(MAX_LAYOUT_ROW_BUTTONS, target.buttons.length || 1));
    cleanupDraftRows(draft);
    return true;
  }

  function moveDraftButtonToNewRow(draft, fromRow, fromIndex, beforeRow) {
    if (!Array.isArray(draft)) return false;
    const source = draft[fromRow];
    const item = source?.buttons?.[fromIndex];
    if (!source || !item) return false;
    const sourceWillBeRemoved = source.buttons.length <= 1;
    source.buttons.splice(fromIndex, 1);
    let insertIndex = Number.isFinite(beforeRow) ? Number(beforeRow) : draft.length;
    if (sourceWillBeRemoved && fromRow < insertIndex) insertIndex -= 1;
    insertIndex = Math.max(0, Math.min(insertIndex, draft.length));
    source.columns = Math.max(1, Math.min(MAX_LAYOUT_ROW_BUTTONS, source.buttons.length || 1));
    draft.splice(insertIndex, 0, { columns: 1, buttons: [item] });
    cleanupDraftRows(draft);
    return true;
  }

  function cancelLayoutEdit(state) {
    if (!state) return false;
    state.layoutModalCleanup?.();
    state.layoutModalCleanup = null;
    state.layoutEditMode = false;
    state.layoutDraftRows = [];
    state.layoutPendingMove = null;
    state.layoutDragNode = null;
    state.layoutDragLocal = false;
    state.layoutError = "";
    return true;
  }

  function bindLayoutModal(ctx, previousScroll) {
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
    dialog.addEventListener("dragstart", () => { dragging = !!ctx.state.layoutDragNode; updateZones(); });
    const endDrag = () => { dragging = false; stopScroll(); };
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
    const resize = () => dialog.style.setProperty("--bot-layout-work-left", `${Math.max(12, ctx.el.content.closest("#appContent").getBoundingClientRect().left + 12)}px`);
    resize();
    window.addEventListener("resize", resize);
    ctx.state.layoutModalCleanup = () => {
      endDrag();
      window.removeEventListener("resize", resize);
      if (dialog.open) dialog.close();
    };
    dialog.showModal();
    scroll.scrollTop = previousScroll;
  }

  function localLayoutChanged(state) {
    return JSON.stringify(serializeLayoutRows(state.layoutDraftRows)) !== state.layoutOriginalRows;
  }

  function canMoveNode(node) {
    return node?.kind === "terminal" && ["ticket", "task", "auto"].includes(node.workflow_type)
      && !!node.button_id && typeof node.parent_path === "string" && !node.removed && !node.is_archived;
  }

  function moveBlockedReason(ctx, node, parentPath) {
    if (ctx.state.layoutSaving) return "Дождитесь сохранения.";
    if (ctx.state.layoutPendingMove) return "Один перенос за сохранение. Сохраните или отмените перенос.";
    if (localLayoutChanged(ctx.state)) return "Сначала сохраните или отмените перестановки текущего уровня.";
    if (!canMoveNode(node)) return "Можно переносить только обычные кнопки с ID и путём из структуры.";
    if (node.parent_path === parentPath) return "Кнопка уже находится на этом уровне.";
    return "";
  }

  function moveDropZone(ctx, parentPath, label) {
    const pending = ctx.state.layoutPendingMove;
    return `<div class="bot-move-drop" data-move-parent="${ctx.escapeHtml(parentPath)}"
      data-move-label="${ctx.escapeHtml(label)}" aria-disabled="${!!pending || localLayoutChanged(ctx.state)}">
      Перенести сюда: ${ctx.escapeHtml(label)}
      ${pending?.payload.parent_path === parentPath ? `<div class="bot-move-preview">После сохранения: ${ctx.escapeHtml(pending.label)}</div>` : ""}
    </div>`;
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
    const children = (node.children_rows || []).flatMap(row => row.buttons || []);
    return `<details class="bot-structure-submenu" data-submenu-key="${ctx.escapeHtml(node.technical_key)}" ${ctx.state.layoutOpenSubmenus?.has(node.technical_key) ? "open" : ""}><summary>Содержимое подменю (${children.length})</summary>
      ${children.map(child => `<div class="bot-structure-child">
        <button type="button" class="${nodeClass(ctx, child)} ${child.button_id && ctx.state.layoutPendingMove?.payload.button_id === child.button_id ? "bot-move-source-pending" : ""}"
          data-button-color-key="${ctx.escapeHtml(child.technical_key || "")}" data-move-source="${ctx.escapeHtml(child.technical_key || "")}"
          draggable="${!ctx.state.layoutPendingMove && !ctx.state.layoutSaving && canMoveNode(child)}">
          <span>${ctx.escapeHtml(child.label)}</span><span class="bot-button-type-tag">${buttonTypeLabel(child)}</span>
        </button>${child.kind === "submenu" ? renderSubmenuContents(ctx, child) : ""}</div>`).join("")}
      ${moveDropZone(ctx, node.full_path, node.label)}
    </details>`;
  }

  function renderLevel(ctx) {
    const previousModal = ctx.el.content.querySelector(".bot-layout-dialog");
    const previousScroll = previousModal?.querySelector(".bot-layout-scroll-area")?.scrollTop || 0;
    ctx.state.layoutModalCleanup?.();
    ctx.state.layoutModalCleanup = null;
    ctx.state.layoutOpenSubmenus = new Set(Array.from(ctx.el.content.querySelectorAll(".bot-structure-submenu[open]"), node => node.dataset.submenuKey));
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
    const addButtonLabel = isGroupParentLevel ? "\u0421\u043e\u0437\u0434\u0430\u0442\u044c child task" : "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c";
    const canEditLayout = canEditLayoutBase && !isGroupParentLevel;
    const toolbarHtml = canEditLayout
      ? `
        <div class="bot-layout-toolbar">
          ${ctx.state.layoutEditMode
            ? `<button type="button" id="saveLayoutBtn" class="action action-primary">Сохранить структуру</button>
               <button type="button" id="cancelLayoutBtn" class="action">\u041e\u0442\u043c\u0435\u043d\u0430</button>
               <span class="bot-layout-help">\u041f\u0435\u0440\u0435\u0442\u0430\u0449\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0438, \u0447\u0442\u043e\u0431\u044b \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0440\u044f\u0434\u043e\u043a. \u0412 \u043e\u0434\u043d\u043e\u043c \u0440\u044f\u0434\u0443 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u0434\u043e 3 \u043a\u043d\u043e\u043f\u043e\u043a.</span>`
            : `<button type="button" id="editLayoutBtn" class="action">Изменить структуру</button>
               ${isSubmenuLevel ? `<button type="button" id="editSubmenuBtn" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0434\u043c\u0435\u043d\u044e</button>` : ""}
               <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>`}
        </div>
      `
      : `<div class="bot-layout-toolbar">
           ${isGroupParentLevel ? `<button type="button" id="editParentTaskBtn" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043c\u0430\u0442\u0435\u0440\u0438\u043d\u0441\u043a\u0443\u044e \u0437\u0430\u0434\u0430\u0447\u0443</button>` : ""}
           ${isSubmenuLevel ? `<button type="button" id="editSubmenuBtn" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0434\u043c\u0435\u043d\u044e</button>` : ""}
           <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>
         </div>`;

    const rowsForRender = ctx.state.layoutEditMode ? (ctx.state.layoutDraftRows || []) : visibleRows;

    ctx.el.content.innerHTML = `
      <div class="bot-buttons-split-layout bot-buttons-v2-list">
        <div class="bot-buttons-split-left">
          ${ctx.renderMenuBindingsPanel()}
          ${ctx.state.layoutEditMode ? `
          <dialog class="bot-layout-edit-shell bot-layout-dialog" aria-labelledby="botLayoutDialogTitle">
            <header class="bot-layout-dialog-head">
              <h2 id="botLayoutDialogTitle">Режим изменения структуры</h2>
          ` : ""}
          ${toolbarHtml}
          ${ctx.state.layoutEditMode ? `
            </header>
            <div class="bot-layout-scroll-frame">
            <div class="bot-layout-scroll-area">
            <div class="bot-move-help" role="status">${ctx.state.layoutPendingMove
              ? `Черновик: ${ctx.escapeHtml(ctx.state.layoutPendingMove.label)} → ${ctx.escapeHtml(ctx.state.layoutPendingMove.targetLabel)}. Сохраните или отмените перенос; другие изменения заблокированы.`
              : "Один межуровневый перенос за сохранение. Кнопка добавится отдельным рядом в конец уровня."}</div>
            ${ctx.state.layoutError ? `<div class="bot-layout-error" role="alert">${ctx.escapeHtml(ctx.state.layoutError)} Можно повторить сохранение или отменить.</div>` : ""}
            ${moveDropZone(ctx, current.fullPath || "", current.kind === "root" ? "Корень меню" : "Текущий уровень")}
            ${current.kind === "submenu" && typeof current.parentPath === "string" ? moveDropZone(ctx, current.parentPath, "На уровень выше") : ""}
          ` : ""}
          <div class="bot-buttons-level-list${ctx.state.messengerView && !ctx.state.layoutEditMode ? " bot-buttons-messenger-view" : ""}">
            ${rowsForRender.length ? rowsForRender.map((row, rowIndex) => `
              ${ctx.state.layoutEditMode ? `<div class="bot-layout-new-row-drop" data-layout-new-row-before="${rowIndex}">\u041d\u043e\u0432\u044b\u0439 \u0440\u044f\u0434 \u0437\u0434\u0435\u0441\u044c</div>` : ""}
              <div class="bot-buttons-row ${ctx.state.layoutEditMode ? "bot-layout-row-edit" : ""}" ${ctx.state.layoutEditMode ? `data-layout-drop-row="${rowIndex}"` : ""}>
                ${(row.buttons || []).map((node, nodeIndex) => `
                  <div class="bot-structure-item">
                  <button
                    type="button"
                    class="${nodeClass(ctx, node)}${node.kind === "submenu" ? " bot-button-submenu" : ""}${ctx.state.layoutPendingMove?.payload.button_id === node.button_id && node.button_id ? " bot-move-source-pending" : ""}"
                    data-button-color-key="${ctx.escapeHtml(node.technical_key || "")}"
                    data-row-index="${rowIndex}"
                    data-node-index="${nodeIndex}"
                    ${ctx.state.layoutEditMode ? `draggable="${!ctx.state.layoutPendingMove && !ctx.state.layoutSaving}" data-layout-drag-button="${rowIndex}:${nodeIndex}"` : ""}
                  >
                    <span>${ctx.escapeHtml(node.label)}</span><span class="bot-button-type-tag">${buttonTypeLabel(node)}</span>
                  </button>
                  ${ctx.state.layoutEditMode && node.kind === "submenu" ? renderSubmenuContents(ctx, node) : ""}
                  </div>
                `).join("")}
              </div>
            `).join("") : `<div class="status">\u041f\u0443\u0441\u0442\u043e</div>`}
            ${ctx.state.layoutEditMode ? `<div class="bot-layout-new-row-drop" data-layout-new-row-before="${rowsForRender.length}">\u041d\u043e\u0432\u044b\u0439 \u0440\u044f\u0434 \u0437\u0434\u0435\u0441\u044c</div>` : ""}
          </div>
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
    ctx.el.content.querySelectorAll(".bot-structure-submenu").forEach(container => {
      container.addEventListener("dragover", event => { event.stopPropagation(); });
      container.addEventListener("drop", event => { event.preventDefault(); event.stopPropagation(); });
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
      ctx.state.layoutDraftRows = normalizeDraftRows(
        visibleRows.map((r) => ({
          columns: layoutColumns(r.columns, (r.buttons || []).length || 1),
          buttons: r.buttons || [],
        })),
      );
      ctx.state.layoutOriginalRows = JSON.stringify(serializeLayoutRows(ctx.state.layoutDraftRows));
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
      if (ctx.state.layoutSaving) return;
      cancelLayoutEdit(ctx.state);
      ctx.render();
    });
    document.getElementById("saveLayoutBtn")?.addEventListener("click", async () => {
      if (ctx.state.layoutSaving) return;
      const level = currentLevel(ctx);
      if (!level?.levelKey || !ctx.state.selectedMenu) return;
      const serializedRows = serializeLayoutRows(ctx.state.layoutDraftRows || []);
      if (!serializedRows) {
        await window.HelpdeskDialogs.alert("\u0412 \u043e\u0434\u043d\u043e\u043c \u0440\u044f\u0434\u0443 \u043d\u0435\u043b\u044c\u0437\u044f \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0431\u043e\u043b\u044c\u0448\u0435 3 \u043a\u043d\u043e\u043f\u043e\u043a.");
        return;
      }
      ctx.setStatus("Сохранение структуры...");
      ctx.state.layoutSaving = true;
      ctx.state.layoutError = "";
      ctx.render();
      try {
        const pending = ctx.state.layoutPendingMove;
        const result = await ctx.api(pending ? "/api/v1/settings/bot-buttons/item/move" : `/api/v1/settings/bot-buttons/layout?menu_type=${encodeURIComponent(ctx.state.selectedMenu)}&level_key=${encodeURIComponent(level.levelKey)}`, {
          method: pending ? "POST" : "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(pending ? pending.payload : { rows: serializedRows }),
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
      bindLayoutEditing(ctx);
      bindLayoutModal(ctx, previousScroll);
      for (const button of ctx.el.content.querySelectorAll("#saveLayoutBtn, #cancelLayoutBtn, .bot-menu-bindings-panel button, .bot-menu-bindings-panel select")) {
        button.disabled = !!ctx.state.layoutSaving || button.closest(".bot-menu-bindings-panel") !== null;
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

  function parseDragRef(value) {
    const [row, index] = String(value || "").split(":").map((item) => Number(item));
    if (!Number.isFinite(row) || !Number.isFinite(index)) return null;
    return { row, index };
  }

  function clearDropClasses(root) {
    root.querySelectorAll(".bot-layout-drop-active, .bot-layout-drop-forbidden").forEach((node) => {
      node.classList.remove("bot-layout-drop-active", "bot-layout-drop-forbidden");
    });
  }

  function bindLayoutEditing(ctx) {
    const draft = ctx.state.layoutDraftRows || [];
    const localAllowed = () => !ctx.state.layoutPendingMove && !ctx.state.layoutSaving && ctx.state.layoutDragLocal;
    const blockLocal = () => {
      ctx.state.layoutError = ctx.state.layoutPendingMove
        ? "Сначала сохраните или отмените межуровневый перенос."
        : "Для межуровневого переноса используйте область вложения или уровень выше.";
      ctx.render();
    };
    ctx.el.content.querySelectorAll("[data-move-source]").forEach(button => {
      button.addEventListener("dragstart", event => {
        if (ctx.state.layoutPendingMove || ctx.state.layoutSaving) { event.preventDefault(); return; }
        const find = rows => {
          for (const row of rows || []) for (const node of row.buttons || []) {
            if (node.technical_key === button.dataset.moveSource) return node;
            const child = find(node.children_rows);
            if (child) return child;
          }
          return null;
        };
        ctx.state.layoutDragNode = find(ctx.state.menuStructure.tree_rows);
        ctx.state.layoutDragLocal = false;
        event.dataTransfer?.setData("text/plain", button.dataset.moveSource);
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      });
      button.addEventListener("dragend", () => {
        ctx.state.layoutDragNode = null;
        ctx.state.layoutDragLocal = false;
        clearDropClasses(ctx.el.content);
      });
    });
    ctx.el.content.querySelectorAll("[data-move-parent]").forEach(zone => {
      zone.addEventListener("dragover", event => {
        event.preventDefault(); event.stopPropagation();
        const reason = moveBlockedReason(ctx, ctx.state.layoutDragNode, zone.dataset.moveParent);
        zone.classList.toggle("bot-layout-drop-active", !reason);
        zone.classList.toggle("bot-layout-drop-forbidden", !!reason);
        zone.setAttribute("aria-disabled", String(!!reason));
        zone.title = reason || "Добавить отдельным рядом в конец уровня";
        const help = ctx.el.content.querySelector(".bot-move-help");
        if (help) help.textContent = reason || "Добавить отдельным рядом в конец уровня после сохранения.";
        if (event.dataTransfer) event.dataTransfer.dropEffect = reason ? "none" : "move";
      });
      zone.addEventListener("dragleave", () => clearDropClasses(ctx.el.content));
      zone.addEventListener("drop", event => {
        event.preventDefault(); event.stopPropagation();
        const node = ctx.state.layoutDragNode;
        const reason = moveBlockedReason(ctx, node, zone.dataset.moveParent);
        if (reason) { ctx.state.layoutError = reason; ctx.render(); return; }
        ctx.state.layoutPendingMove = {
          label: node.label,
          targetLabel: zone.dataset.moveLabel,
          payload: { button_id: node.button_id, menu_type: ctx.state.selectedMenu,
            parent_path: zone.dataset.moveParent, new_row: true, target_index: 0 },
        };
        ctx.state.layoutDragNode = null;
        ctx.state.layoutError = "";
        ctx.render();
      });
    });
    ctx.el.content.querySelectorAll("[data-layout-drag-button]").forEach((btn) => {
      btn.addEventListener("dragstart", (event) => {
        if (ctx.state.layoutPendingMove || ctx.state.layoutSaving) { event.preventDefault(); return; }
        const ref = parseDragRef(btn.dataset.layoutDragButton);
        ctx.state.layoutDragNode = draft[ref?.row]?.buttons?.[ref?.index];
        ctx.state.layoutDragLocal = true;
        event.dataTransfer?.setData("text/plain", btn.dataset.layoutDragButton || "");
        event.dataTransfer?.setData("application/x-bot-layout-button", btn.dataset.layoutDragButton || "");
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
        btn.classList.add("bot-layout-dragging");
      });
      btn.addEventListener("dragend", () => {
        ctx.state.layoutDragNode = null;
        ctx.state.layoutDragLocal = false;
        btn.classList.remove("bot-layout-dragging");
        clearDropClasses(ctx.el.content);
      });
      btn.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const target = draft[Number(btn.dataset.rowIndex)];
        const allowed = localAllowed() && (target?.buttons.includes(ctx.state.layoutDragNode) || (target?.buttons.length || 0) < MAX_LAYOUT_ROW_BUTTONS);
        btn.classList.toggle("bot-layout-drop-active", allowed);
        btn.classList.toggle("bot-layout-drop-forbidden", !allowed);
      });
      btn.addEventListener("dragleave", () => btn.classList.remove("bot-layout-drop-active"));
      btn.addEventListener("drop", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!localAllowed()) { blockLocal(); return; }
        const from = parseDragRef(event.dataTransfer?.getData("application/x-bot-layout-button") || event.dataTransfer?.getData("text/plain"));
        const toRow = Number(btn.dataset.rowIndex);
        const toIndex = Number(btn.dataset.nodeIndex);
        clearDropClasses(ctx.el.content);
        if (!from || Number.isNaN(toRow) || Number.isNaN(toIndex)) return;
        if (!moveDraftButton(draft, from.row, from.index, toRow, toIndex)) {
          await window.HelpdeskDialogs.alert("\u0412 \u0440\u044f\u0434\u0443 \u0443\u0436\u0435 3 \u043a\u043d\u043e\u043f\u043a\u0438.");
          return;
        }
        ctx.render();
      });
    });

    ctx.el.content.querySelectorAll("[data-layout-drop-row]").forEach((rowEl) => {
      rowEl.addEventListener("dragover", (event) => {
        event.preventDefault();
        const idx = Number(rowEl.dataset.layoutDropRow);
        const from = parseDragRef(event.dataTransfer?.getData("application/x-bot-layout-button") || event.dataTransfer?.getData("text/plain"));
        const targetLen = draft[idx]?.buttons?.length || 0;
        const sameRow = ctx.state.layoutDragNode && draft[idx]?.buttons.includes(ctx.state.layoutDragNode);
        const allowed = localAllowed() && (sameRow || targetLen < MAX_LAYOUT_ROW_BUTTONS);
        rowEl.classList.toggle("bot-layout-drop-active", !!allowed);
        rowEl.classList.toggle("bot-layout-drop-forbidden", !allowed);
      });
      rowEl.addEventListener("dragleave", () => {
        rowEl.classList.remove("bot-layout-drop-active", "bot-layout-drop-forbidden");
      });
      rowEl.addEventListener("drop", async (event) => {
        event.preventDefault();
        if (!localAllowed()) { blockLocal(); return; }
        const from = parseDragRef(event.dataTransfer?.getData("application/x-bot-layout-button") || event.dataTransfer?.getData("text/plain"));
        const toRow = Number(rowEl.dataset.layoutDropRow);
        clearDropClasses(ctx.el.content);
        if (!from || Number.isNaN(toRow)) return;
        if (!moveDraftButton(draft, from.row, from.index, toRow, null)) {
          await window.HelpdeskDialogs.alert("\u0412 \u0440\u044f\u0434\u0443 \u0443\u0436\u0435 3 \u043a\u043d\u043e\u043f\u043a\u0438.");
          return;
        }
        ctx.render();
      });
    });

    ctx.el.content.querySelectorAll("[data-layout-new-row-before]").forEach((zone) => {
      zone.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.toggle("bot-layout-drop-active", !!localAllowed());
        zone.classList.toggle("bot-layout-drop-forbidden", !localAllowed());
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("bot-layout-drop-active"));
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
        if (!localAllowed()) { blockLocal(); return; }
        const from = parseDragRef(event.dataTransfer?.getData("application/x-bot-layout-button") || event.dataTransfer?.getData("text/plain"));
        const beforeRow = Number(zone.dataset.layoutNewRowBefore);
        clearDropClasses(ctx.el.content);
        if (!from || Number.isNaN(beforeRow)) return;
        moveDraftButtonToNewRow(draft, from.row, from.index, beforeRow);
        ctx.render();
      });
    });
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
