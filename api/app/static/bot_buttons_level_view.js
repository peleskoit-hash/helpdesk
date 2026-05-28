(function initBotButtonsLevelView(global) {
  function nodeClass(ctx, node) {
    if (ctx.state.layoutEditMode) return "bot-buttons-level-item";
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
    const addButtonLabel = isGroupParentLevel ? "Создать child task" : "Добавить";
    const canEditLayout = canEditLayoutBase && !isGroupParentLevel;
    const toolbarHtml = canEditLayout
      ? `
        <div class="bot-layout-toolbar">
          ${ctx.state.layoutEditMode
            ? `<button type="button" id="saveLayoutBtn" class="action action-primary">Сохранить раскладку</button>
               <button type="button" id="cancelLayoutBtn" class="action">Отмена</button>`
            : `<button type="button" id="editLayoutBtn" class="action">Изменить порядок</button>
               ${isGroupParentLevel ? `<button type="button" id="editParentTaskBtn" class="action">Редактирование материнской задачи</button>` : ""}
               <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>`}
        </div>
      `
      : `<div class="bot-layout-toolbar">
           ${isGroupParentLevel ? `<button type="button" id="editParentTaskBtn" class="action">Редактирование материнской задачи</button>` : ""}
           <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>
         </div>`;

    const rowsForRender = ctx.state.layoutEditMode ? (ctx.state.layoutDraftRows || []) : visibleRows;

    ctx.el.content.innerHTML = `
      <div class="bot-buttons-split-layout">
        <div class="bot-buttons-split-left">
          ${ctx.renderMenuBindingsPanel()}
          ${toolbarHtml}
          <div class="bot-buttons-level-list">
            ${rowsForRender.length ? rowsForRender.map((row, rowIndex) => `
              <div class="bot-buttons-row ${ctx.state.layoutEditMode ? "bot-layout-row-edit" : ""}">
                ${ctx.state.layoutEditMode ? `
                  <div class="bot-layout-row-head">
                    <label>Ряд ${rowIndex + 1}</label>
                    <select class="input bot-layout-cols" data-layout-row="${rowIndex}">
                      <option value="1" ${Number(row.columns || 1) === 1 ? "selected" : ""}>1 кнопка в ряду</option>
                      <option value="2" ${Number(row.columns || 1) === 2 ? "selected" : ""}>2 кнопки в ряду</option>
                    </select>
                    <button type="button" class="action" data-layout-add-row="${rowIndex}">+ Ряд ниже</button>
                    <button type="button" class="action" data-layout-del-row="${rowIndex}">Удалить ряд</button>
                  </div>
                ` : ""}
                ${(row.buttons || []).map((node, nodeIndex) => `
                  <button
                    type="button"
                    class="${nodeClass(ctx, node)}"
                    data-row-index="${rowIndex}"
                    data-node-index="${nodeIndex}"
                  >
                    ${ctx.escapeHtml(node.label)}
                  </button>
                  ${ctx.state.layoutEditMode ? `
                  <div class="bot-layout-item-actions">
                    <button type="button" class="action" data-move-left="${rowIndex}:${nodeIndex}">←</button>
                    <button type="button" class="action" data-move-right="${rowIndex}:${nodeIndex}">→</button>
                    <button type="button" class="action" data-move-up="${rowIndex}:${nodeIndex}">↑ ряд</button>
                    <button type="button" class="action" data-move-down="${rowIndex}:${nodeIndex}">↓ ряд</button>
                  </div>` : ""}
                `).join("")}
              </div>
            `).join("") : `<div class="status">Пусто</div>`}
          </div>
          ${!ctx.state.layoutEditMode && !isArchiveLevel && removedCount > 0 ? `
            <div class="bot-buttons-archive-bottom">
              <button type="button" id="openRemovedArchiveBtn" class="bot-buttons-level-item bot-button-leaf-removed bot-button-archive-main">
                Архив удалённых (${removedCount})
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
        if (nextRows.length > 0) {
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
        if (node.kind === "terminal") {
          await ctx.openEditor(node);
          return;
        }
      });
    });

    const editLayoutBtn = document.getElementById("editLayoutBtn");
    const addLevelItemBtn = document.getElementById("addLevelItemBtn");
    const editParentTaskBtn = document.getElementById("editParentTaskBtn");
    const cancelLayoutBtn = document.getElementById("cancelLayoutBtn");
    const saveLayoutBtn = document.getElementById("saveLayoutBtn");
    editLayoutBtn?.addEventListener("click", () => {
      ctx.state.layoutEditMode = true;
      ctx.state.layoutDraftRows = cloneRows(
        ctx,
        visibleRows.map((r) => ({ columns: Number(r.columns || 1) === 2 ? 2 : 1, buttons: r.buttons || [] })),
      );
      ctx.render();
    });
    addLevelItemBtn?.addEventListener("click", async () => {
      await ctx.createLevelItem();
    });
    editParentTaskBtn?.addEventListener("click", async () => {
      if (typeof ctx.openParentTaskEditor === "function") {
        await ctx.openParentTaskEditor();
      }
    });
    cancelLayoutBtn?.addEventListener("click", () => {
      ctx.state.layoutEditMode = false;
      ctx.state.layoutDraftRows = [];
      ctx.render();
    });
    saveLayoutBtn?.addEventListener("click", async () => {
      const level = currentLevel(ctx);
      if (!level?.levelKey || !ctx.state.selectedMenu) return;
      for (const row of ctx.state.layoutDraftRows || []) {
        if ((row.buttons || []).length > 2) {
          alert("В одном ряду нельзя сохранить больше 2 кнопок.");
          return;
        }
      }
      const serializedRows = [];
      for (const row of ctx.state.layoutDraftRows || []) {
        const cols = Number(row.columns || 1) === 2 ? 2 : 1;
        const keys = (row.buttons || []).map((n) => n.technical_key).filter(Boolean);
        if (!keys.length) continue;
        if (cols === 1) {
          for (const key of keys) serializedRows.push({ columns: 1, button_keys: [key] });
        } else {
          for (let i = 0; i < keys.length; i += 2) serializedRows.push({ columns: 2, button_keys: keys.slice(i, i + 2) });
        }
      }
      ctx.setStatus("Сохранение раскладки...");
      await ctx.api(`/api/v1/settings/bot-buttons/layout?menu_type=${encodeURIComponent(ctx.state.selectedMenu)}&level_key=${encodeURIComponent(level.levelKey)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ rows: serializedRows }),
      });
      ctx.state.layoutEditMode = false;
      ctx.state.layoutDraftRows = [];
      await ctx.refreshMenuStructure();
      ctx.render();
      ctx.setStatus("Раскладка сохранена");
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
      if (!window.confirm("Вы уверены что хотите удалить меню кнопок?")) return;
      if (!window.confirm("Вы ТОЧНО уверены что хотите удалить меню кнопок?")) return;
      ctx.setStatus("Удаление меню...");
      await ctx.api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(ctx.state.selectedMenu)}`, { method: "DELETE" });
      await ctx.loadMenuTypes();
      ctx.setStatus("Меню удалено");
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
      const draft = ctx.state.layoutDraftRows || [];
      ctx.el.content.querySelectorAll("[data-layout-row]").forEach((input) => {
        input.addEventListener("change", () => {
          const idx = Number(input.dataset.layoutRow);
          if (Number.isNaN(idx) || !draft[idx]) return;
          draft[idx].columns = Number(input.value) === 2 ? 2 : 1;
        });
      });
      ctx.el.content.querySelectorAll("[data-layout-add-row]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.layoutAddRow);
          const at = Number.isNaN(idx) ? draft.length - 1 : idx;
          draft.splice(at + 1, 0, { columns: 1, buttons: [] });
          ctx.render();
        });
      });
      ctx.el.content.querySelectorAll("[data-layout-del-row]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.layoutDelRow);
          if (Number.isNaN(idx) || !draft[idx]) return;
          if ((draft[idx].buttons || []).length > 0) {
            alert("Можно удалить только пустой ряд.");
            return;
          }
          draft.splice(idx, 1);
          if (!draft.length) draft.push({ columns: 1, buttons: [] });
          ctx.render();
        });
      });
      const cleanupEmptyRows = () => {
        for (let idx = draft.length - 1; idx >= 0; idx -= 1) {
          if ((draft[idx]?.buttons || []).length === 0) draft.splice(idx, 1);
        }
        if (!draft.length) draft.push({ columns: 1, buttons: [] });
      };
      const moveBetweenRows = (fromRow, fromIndex, toRow) => {
        if (!draft[fromRow] || !draft[toRow] || !draft[fromRow].buttons?.[fromIndex]) return;
        if ((draft[toRow].buttons || []).length >= 2) {
          alert("В целевом ряду уже 2 кнопки.");
          return;
        }
        const [item] = draft[fromRow].buttons.splice(fromIndex, 1);
        draft[toRow].buttons.push(item);
        cleanupEmptyRows();
        ctx.render();
      };
      const moveToOwnRow = (fromRow, fromIndex, direction) => {
        if (!draft[fromRow] || !draft[fromRow].buttons?.[fromIndex]) return;
        const [item] = draft[fromRow].buttons.splice(fromIndex, 1);
        const baseIndex = direction === "up" ? fromRow : fromRow + 1;
        const insertIndex = Math.max(0, Math.min(baseIndex, draft.length));
        draft.splice(insertIndex, 0, { columns: 1, buttons: [item] });
        cleanupEmptyRows();
        ctx.render();
      };
      const moveUpOrDown = (fromRow, fromIndex, direction) => {
        if (!draft[fromRow] || !draft[fromRow].buttons?.[fromIndex]) return;
        const targetRow = direction === "up" ? fromRow - 1 : fromRow + 1;
        if (targetRow >= 0 && targetRow < draft.length) {
          const target = draft[targetRow];
          const targetCols = Number(target?.columns || 1) === 2 ? 2 : 1;
          const targetLen = (target?.buttons || []).length;
          if (targetCols === 2 && targetLen < 2) {
            moveBetweenRows(fromRow, fromIndex, targetRow);
            return;
          }
        }
        moveToOwnRow(fromRow, fromIndex, direction);
      };
      ctx.el.content.querySelectorAll("[data-move-left]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const [r, i] = String(btn.dataset.moveLeft || "").split(":").map(Number);
          if (!draft[r] || Number.isNaN(i)) return;
          if (i <= 0) return;
          const arr = draft[r].buttons || [];
          [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
          ctx.render();
        });
      });
      ctx.el.content.querySelectorAll("[data-move-right]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const [r, i] = String(btn.dataset.moveRight || "").split(":").map(Number);
          const arr = draft[r]?.buttons || [];
          if (i < 0 || i >= arr.length - 1) return;
          [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
          ctx.render();
        });
      });
      ctx.el.content.querySelectorAll("[data-move-up]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const [r, i] = String(btn.dataset.moveUp || "").split(":").map(Number);
          if (!draft[r] || Number.isNaN(i)) return;
          moveUpOrDown(r, i, "up");
        });
      });
      ctx.el.content.querySelectorAll("[data-move-down]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const [r, i] = String(btn.dataset.moveDown || "").split(":").map(Number);
          if (!draft[r] || Number.isNaN(i)) return;
          moveUpOrDown(r, i, "down");
        });
      });
    }

    const archiveBtn = document.getElementById("openRemovedArchiveBtn");
    archiveBtn?.addEventListener("click", () => {
      const rowsForArchive = buildArchiveRowsFromStructure(ctx);
      ctx.state.levelStack.push({ label: "Архив удалённых", rows: rowsForArchive, levelKey: `${ctx.state.selectedMenu}|level|__removed__` });
      ctx.render();
    });

    const renameSectionBtn = document.getElementById("renameSectionBtn");
    const resetSectionLabelBtn = document.getElementById("resetSectionLabelBtn");
    renameSectionBtn?.addEventListener("click", async () => {
      const currentPreview = ctx.state.preview || {};
      const initial = (currentPreview.item?.button_label_effective || currentPreview.label || "").trim();
      const next = window.prompt("Название в боте", initial);
      if (next === null) return;
      await ctx.saveLabelOverrideForNode(currentPreview, next, true);
    });
    resetSectionLabelBtn?.addEventListener("click", async () => {
      const currentPreview = ctx.state.preview || {};
      if (!window.confirm("Сбросить переопределённое название и вернуть название из txt?")) return;
      await ctx.saveLabelOverrideForNode(currentPreview, "", false);
    });
  }

  global.BotButtonsLevelView = {
    nodeClass,
    cloneRows,
    currentLevel,
    isLayoutEditableLevel,
    renderLevel,
    buildArchiveRowsFromStructure,
  };
})(window);
