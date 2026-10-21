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
    state.layoutEditMode = false;
    state.layoutDraftRows = [];
    return true;
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
        is_db_created: !!leaf.is_db_created,
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

  function renderLevel(ctx) {
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
            ? `<button type="button" id="saveLayoutBtn" class="action action-primary">\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u043f\u043e\u0440\u044f\u0434\u043e\u043a</button>
               <button type="button" id="cancelLayoutBtn" class="action">\u041e\u0442\u043c\u0435\u043d\u0430</button>
               <span class="bot-layout-help">\u041f\u0435\u0440\u0435\u0442\u0430\u0449\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0438, \u0447\u0442\u043e\u0431\u044b \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0440\u044f\u0434\u043e\u043a. \u0412 \u043e\u0434\u043d\u043e\u043c \u0440\u044f\u0434\u0443 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u0434\u043e 3 \u043a\u043d\u043e\u043f\u043e\u043a.</span>`
            : `<button type="button" id="editLayoutBtn" class="action">\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0440\u044f\u0434\u043e\u043a</button>
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
      <div class="bot-buttons-split-layout">
        <div class="bot-buttons-split-left">
          ${ctx.renderMenuBindingsPanel()}
          ${ctx.state.layoutEditMode ? `
          <div class="bot-layout-edit-shell">
            <div class="bot-layout-mode-badge">\u0420\u0435\u0436\u0438\u043c \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043f\u043e\u0440\u044f\u0434\u043a\u0430</div>
          ` : ""}
          ${toolbarHtml}
          <div class="bot-buttons-level-list">
            ${rowsForRender.length ? rowsForRender.map((row, rowIndex) => `
              ${ctx.state.layoutEditMode ? `<div class="bot-layout-new-row-drop" data-layout-new-row-before="${rowIndex}">\u041d\u043e\u0432\u044b\u0439 \u0440\u044f\u0434 \u0437\u0434\u0435\u0441\u044c</div>` : ""}
              <div class="bot-buttons-row ${ctx.state.layoutEditMode ? "bot-layout-row-edit" : ""}" ${ctx.state.layoutEditMode ? `data-layout-drop-row="${rowIndex}"` : ""}>
                ${(row.buttons || []).map((node, nodeIndex) => `
                  <button
                    type="button"
                    class="${nodeClass(ctx, node)}"
                    data-row-index="${rowIndex}"
                    data-node-index="${nodeIndex}"
                    ${ctx.state.layoutEditMode ? `draggable="true" data-layout-drag-button="${rowIndex}:${nodeIndex}"` : ""}
                  >
                    ${ctx.escapeHtml(node.label)}
                  </button>
                `).join("")}
              </div>
            `).join("") : `<div class="status">\u041f\u0443\u0441\u0442\u043e</div>`}
            ${ctx.state.layoutEditMode ? `<div class="bot-layout-new-row-drop" data-layout-new-row-before="${rowsForRender.length}">\u041d\u043e\u0432\u044b\u0439 \u0440\u044f\u0434 \u0437\u0434\u0435\u0441\u044c</div>` : ""}
          </div>
          ${ctx.state.layoutEditMode ? "</div>" : ""}
          ${!ctx.state.layoutEditMode && !isArchiveLevel && removedCount > 0 ? `
            <div class="bot-buttons-archive-bottom">
              <button type="button" id="openRemovedArchiveBtn" class="bot-buttons-level-item bot-button-leaf-removed bot-button-archive-main">
                \u0410\u0440\u0445\u0438\u0432 \u0443\u0434\u0430\u043b\u0435\u043d\u043d\u044b\u0445 (${removedCount})
              </button>
            </div>
          ` : ""}
        </div>
        <div class="bot-buttons-split-right" id="botButtonsRightPanel">
          ${ctx.renderRightPanel()}
        </div>
      </div>
    `;

    ctx.el.content.querySelectorAll("[data-row-index][data-node-index]").forEach((btn) => {
      btn.addEventListener("mouseenter", () => {
        if (ctx.state.layoutEditMode) return;
        const row = rows[Number(btn.dataset.rowIndex)];
        const mappedRow = visibleRows[Number(btn.dataset.rowIndex)];
        const sourceRow = mappedRow || row;
        const node = sourceRow?.buttons?.[Number(btn.dataset.nodeIndex)];
        if (!node) return;
        ctx.state.preview = ctx.buildPreviewFromNode(node);
        const panel = document.getElementById("botButtonsRightPanel");
        if (panel) panel.innerHTML = ctx.renderRightPanel();
        ctx.loadPreviewItem(node);
      });
      btn.addEventListener("click", async () => {
        if (ctx.state.layoutEditMode) return;
        if (!ctx.confirmDiscard()) return;
        ctx.discardEditorChanges();
        const row = rows[Number(btn.dataset.rowIndex)];
        const node = row?.buttons?.[Number(btn.dataset.nodeIndex)];
        if (!node) return;
        ctx.state.preview = ctx.buildPreviewFromNode(node);
        {
          const panel = document.getElementById("botButtonsRightPanel");
          if (panel) panel.innerHTML = ctx.renderRightPanel();
        }
        ctx.loadPreviewItem(node);
        const nextRows = Array.isArray(node.children_rows) ? node.children_rows : [];
        if (node.kind === "submenu" || node.kind === "group_task_parent" || nextRows.length > 0) {
          ctx.state.levelStack.push({
            label: node.label,
            rows: nextRows,
            levelKey: node.level_key || null,
            fullPath: node.full_path || "",
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
      ctx.state.layoutEditMode = true;
      ctx.state.layoutDraftRows = normalizeDraftRows(
        visibleRows.map((r) => ({
          columns: layoutColumns(r.columns, (r.buttons || []).length || 1),
          buttons: r.buttons || [],
        })),
      );
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
      cancelLayoutEdit(ctx.state);
      ctx.render();
    });
    document.getElementById("saveLayoutBtn")?.addEventListener("click", async () => {
      const level = currentLevel(ctx);
      if (!level?.levelKey || !ctx.state.selectedMenu) return;
      const serializedRows = serializeLayoutRows(ctx.state.layoutDraftRows || []);
      if (!serializedRows) {
        alert("\u0412 \u043e\u0434\u043d\u043e\u043c \u0440\u044f\u0434\u0443 \u043d\u0435\u043b\u044c\u0437\u044f \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0431\u043e\u043b\u044c\u0448\u0435 3 \u043a\u043d\u043e\u043f\u043e\u043a.");
        return;
      }
      ctx.setStatus("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435 \u043f\u043e\u0440\u044f\u0434\u043a\u0430...");
      const result = await ctx.api(`/api/v1/settings/bot-buttons/layout?menu_type=${encodeURIComponent(ctx.state.selectedMenu)}&level_key=${encodeURIComponent(level.levelKey)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ rows: serializedRows }),
      });
      ctx.state.layoutEditMode = false;
      ctx.state.layoutDraftRows = [];
      if (result?.structure) {
        ctx.state.menuStructure = result.structure;
        const refreshedRows = level.levelKey === result.structure.root_level_key
          ? (result.structure.tree_rows || [])
          : findRowsForLevelKey(result.structure.tree_rows || [], level.levelKey);
        if (refreshedRows) {
          const stackIndex = Math.max(0, (ctx.state.levelStack || []).length - 1);
          ctx.state.levelStack[stackIndex] = {
            ...(ctx.state.levelStack[stackIndex] || {}),
            rows: refreshedRows,
            levelKey: level.levelKey,
          };
        } else {
          await ctx.refreshMenuStructure();
        }
      } else {
        await ctx.refreshMenuStructure();
      }
      ctx.render();
      ctx.setStatus("\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d");
      global.HelpdeskAppShell?.showSaved?.(ctx.el?.content?.closest(".panel.bot-buttons-panel"));
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
      if (!window.confirm("\u0412\u044b \u0443\u0432\u0435\u0440\u0435\u043d\u044b, \u0447\u0442\u043e \u0445\u043e\u0442\u0438\u0442\u0435 \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043c\u0435\u043d\u044e \u043a\u043d\u043e\u043f\u043e\u043a?")) return;
      if (!window.confirm("\u0412\u044b \u0422\u041e\u0427\u041d\u041e \u0443\u0432\u0435\u0440\u0435\u043d\u044b, \u0447\u0442\u043e \u0445\u043e\u0442\u0438\u0442\u0435 \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043c\u0435\u043d\u044e \u043a\u043d\u043e\u043f\u043e\u043a?")) return;
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
    }

    document.getElementById("openRemovedArchiveBtn")?.addEventListener("click", () => {
      const rowsForArchive = buildArchiveRowsFromStructure(ctx);
      ctx.state.levelStack.push({ label: "\u0410\u0440\u0445\u0438\u0432 \u0443\u0434\u0430\u043b\u0435\u043d\u043d\u044b\u0445", rows: rowsForArchive, levelKey: `${ctx.state.selectedMenu}|level|__removed__` });
      ctx.render();
    });

    document.getElementById("renameSectionBtn")?.addEventListener("click", async () => {
      const currentPreview = ctx.state.preview || {};
      const initial = (currentPreview.item?.button_label_effective || currentPreview.label || "").trim();
      const next = window.prompt("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0432 \u0431\u043e\u0442\u0435", initial);
      if (next === null) return;
      await ctx.saveLabelOverrideForNode(currentPreview, next, true);
    });
    document.getElementById("resetSectionLabelBtn")?.addEventListener("click", async () => {
      const currentPreview = ctx.state.preview || {};
      if (!window.confirm("\u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u043f\u0435\u0440\u0435\u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0435\u043d\u043d\u043e\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0438 \u0432\u0435\u0440\u043d\u0443\u0442\u044c \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0438\u0437 \u0411\u0414?")) return;
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
    ctx.el.content.querySelectorAll("[data-layout-drag-button]").forEach((btn) => {
      btn.addEventListener("dragstart", (event) => {
        event.dataTransfer?.setData("text/plain", btn.dataset.layoutDragButton || "");
        event.dataTransfer?.setData("application/x-bot-layout-button", btn.dataset.layoutDragButton || "");
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
        btn.classList.add("bot-layout-dragging");
      });
      btn.addEventListener("dragend", () => {
        btn.classList.remove("bot-layout-dragging");
        clearDropClasses(ctx.el.content);
      });
      btn.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.stopPropagation();
        btn.classList.add("bot-layout-drop-active");
      });
      btn.addEventListener("dragleave", () => btn.classList.remove("bot-layout-drop-active"));
      btn.addEventListener("drop", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const from = parseDragRef(event.dataTransfer?.getData("application/x-bot-layout-button") || event.dataTransfer?.getData("text/plain"));
        const toRow = Number(btn.dataset.rowIndex);
        const toIndex = Number(btn.dataset.nodeIndex);
        clearDropClasses(ctx.el.content);
        if (!from || Number.isNaN(toRow) || Number.isNaN(toIndex)) return;
        if (!moveDraftButton(draft, from.row, from.index, toRow, toIndex)) {
          alert("\u0412 \u0440\u044f\u0434\u0443 \u0443\u0436\u0435 3 \u043a\u043d\u043e\u043f\u043a\u0438.");
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
        const sameRow = from && from.row === idx;
        rowEl.classList.toggle("bot-layout-drop-active", sameRow || targetLen < MAX_LAYOUT_ROW_BUTTONS);
        rowEl.classList.toggle("bot-layout-drop-forbidden", !sameRow && targetLen >= MAX_LAYOUT_ROW_BUTTONS);
      });
      rowEl.addEventListener("dragleave", () => {
        rowEl.classList.remove("bot-layout-drop-active", "bot-layout-drop-forbidden");
      });
      rowEl.addEventListener("drop", (event) => {
        event.preventDefault();
        const from = parseDragRef(event.dataTransfer?.getData("application/x-bot-layout-button") || event.dataTransfer?.getData("text/plain"));
        const toRow = Number(rowEl.dataset.layoutDropRow);
        clearDropClasses(ctx.el.content);
        if (!from || Number.isNaN(toRow)) return;
        if (!moveDraftButton(draft, from.row, from.index, toRow, null)) {
          alert("\u0412 \u0440\u044f\u0434\u0443 \u0443\u0436\u0435 3 \u043a\u043d\u043e\u043f\u043a\u0438.");
          return;
        }
        ctx.render();
      });
    });

    ctx.el.content.querySelectorAll("[data-layout-new-row-before]").forEach((zone) => {
      zone.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("bot-layout-drop-active");
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("bot-layout-drop-active"));
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
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
