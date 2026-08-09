(function initTicketControls(global) {
  let pendingAdminAction = null;

  function isFinalProblemButton(item) {
    return item && item.kind !== "submenu";
  }

  function assigneeOptionsHtml(ctx, selectedId) {
    const { state, escapeHtml } = ctx;
    const options = [`<option value="" ${selectedId ? "" : "selected"}>Нераспределён</option>`];
    const hasSelectedInSupport = Boolean(
      selectedId && state.supportAgents.some((agent) => agent.id === selectedId)
    );
    if (selectedId && !hasSelectedInSupport) {
      const currentAssigneeLabel =
        state.card?.special_assignee_name ||
        state.card?.assignee_name ||
        "Текущий ответственный";
      options.push(
        `<option value="${escapeHtml(selectedId)}" selected>${escapeHtml(currentAssigneeLabel)}</option>`
      );
    }
    for (const agent of state.supportAgents) {
      options.push(
        `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.full_name)}</option>`
      );
    }
    return options.join("");
  }

  function renderTicketAssigneeSelect(ctx) {
    const { state, el } = ctx;
    if (!state.card || !el.ticketAssigneeSelect) return;
    el.ticketAssigneeSelect.innerHTML = assigneeOptionsHtml(ctx, state.card.assignee_id);
    if (state.card.assignee_id) {
      el.ticketAssigneeSelect.value = state.card.assignee_id;
    }
  }

  function renderWrongProblemRows(ctx, rows, container) {
    container.innerHTML = "";
    for (const row of rows || []) {
      const rowEl = document.createElement("div");
      rowEl.className = "wrong-problem-row";
      for (const item of row.buttons || []) {
        const wrap = document.createElement("div");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `wrong-problem-item ${item.kind === "submenu" ? "submenu" : ""}`.trim();
        btn.textContent = item.kind === "submenu" ? `${item.label} ▸` : item.label;
        wrap.appendChild(btn);

        if (item.kind === "submenu") {
          const children = document.createElement("div");
          children.className = "wrong-problem-children hidden";
          renderWrongProblemRows(ctx, item.children || [], children);
          btn.addEventListener("click", () => children.classList.toggle("hidden"));
          wrap.appendChild(children);
        } else {
          btn.addEventListener("click", () => chooseWrongProblem(ctx, item));
        }

        rowEl.appendChild(wrap);
      }
      container.appendChild(rowEl);
    }
  }

  async function openWrongProblemModal(ctx) {
    const { state, el, api, setStatus, showError } = ctx;
    try {
      setStatus("Загружаю меню проблем...");
      state.wrongProblemMenu = await api(`/api/v1/tickets/${state.ticketId}/problem-menu`);
      renderWrongProblemRows(ctx, state.wrongProblemMenu.rows || [], el.wrongProblemMenu);
      el.wrongProblemModal.classList.remove("hidden");
      setStatus("Выберите корректный пункт проблемы");
    } catch (e) {
      showError(e.message);
    }
  }

  function closeWrongProblemModal(ctx) {
    const { el } = ctx;
    el.wrongProblemModal.classList.add("hidden");
  }

  function openSpecialConfirmModal(ctx) {
    const { el } = ctx;
    if (!el.specialConfirmModal) return;
    el.specialConfirmModal.classList.remove("hidden");
  }

  function closeSpecialConfirmModal(ctx) {
    const { el } = ctx;
    if (!el.specialConfirmModal) return;
    el.specialConfirmModal.classList.add("hidden");
  }

  function openAdminActionModal(ctx, kind) {
    const { state, el } = ctx;
    pendingAdminAction = kind;
    const isDelete = kind === "delete";
    const isTask = (state.card?.workflow_type || "ticket") === "task";
    el.adminActionModalTitle.textContent = isDelete ? "Удаление заявки" : "Принудительное закрытие";
    el.adminActionModalText.textContent = isDelete
      ? "Вы уверены, что хотите удалить заявку?"
      : (isTask
        ? "Вы уверены, что хотите принудительно закрыть эту задачу?"
        : "Вы уверены, что хотите принудительно закрыть эту заявку?");
    el.confirmAdminActionBtn.classList.toggle("danger", isDelete);
    el.confirmAdminActionBtn.classList.toggle("primary", !isDelete);
    el.adminActionModal.classList.remove("hidden");
  }

  function closeAdminActionModal(ctx) {
    const { el } = ctx;
    pendingAdminAction = null;
    el.adminActionModal.classList.add("hidden");
  }

  async function runAdminAction(ctx) {
    const { state, apiPost, setStatus, loadCard, loadTabCounts, showError } = ctx;
    if (!state.card || !pendingAdminAction) return;
    const payload = { ticket_ids: [state.card.id] };
    if (pendingAdminAction === "close") {
      setStatus("Принудительно закрываю заявку...");
      const result = await apiPost("/api/v1/tickets/admin/close", payload);
      await Promise.all([loadCard(), loadTabCounts()]);
      setStatus(`Закрыто: ${result.closed}, уже закрыты: ${result.already_closed}`);
      return;
    }
    setStatus("Удаляю заявку...");
    const result = await apiPost("/api/v1/tickets/admin/delete", payload);
    if (result.file_delete_warnings?.length) {
      showError(`Заявка удалена.\nПредупреждения:\n${result.file_delete_warnings.join("\n")}`);
    }
    const returnUrl = `/tickets?tab=${encodeURIComponent(state.tab)}`;
    if (window.HelpdeskAppShell?.navigate) {
      await window.HelpdeskAppShell.navigate(returnUrl);
    } else {
      window.location.href = returnUrl;
    }
  }

  async function markTicketSpecial(ctx) {
    const { state, apiPost, setStatus, loadCard, loadTabCounts, showError } = ctx;
    try {
      setStatus("Перевожу заявку в особые...");
      await apiPost(`/api/v1/tickets/${state.ticketId}/special`);
      closeSpecialConfirmModal(ctx);
      await Promise.all([loadCard(), loadTabCounts()]);
      setStatus("Заявка переведена в особые");
    } catch (e) {
      showError(e.message);
    }
  }

  async function chooseWrongProblem(ctx, item) {
    const { state, el, apiPost, setStatus, loadCard, showError, setVisible } = ctx;
    if (!isFinalProblemButton(item)) return;
    const confirmText = `Вы уверены что хотите отправить клиенту коррекционное сообщение с выбором "${item.label}"?`;
    if (!window.confirm(confirmText)) return;
    try {
      setStatus("Применяю результат...");
      await apiPost(`/api/v1/tickets/${state.ticketId}/wrong-problem`, { button_key: item.key });
      if (state.card) state.card.wrong_problem_used = true;
      setVisible(el.wrongProblemBtn, false);
      closeWrongProblemModal(ctx);
      await loadCard();
      if (state.card?.workflow_type === "task") {
        setStatus("Результат применён. Задача закрыта и создана корректная заявка.");
      } else {
        setStatus("Результат применён успешно");
      }
    } catch (e) {
      showError(e.message);
      if ((e.message || "").includes("уже закрыта")) {
        await loadCard({ silent: true });
      }
    }
  }

  function renderTicketControlsView(ctx) {
    const { state, el, setVisible, minutesLeftToDue, greetingAllowed } = ctx;
    const card = state.card;
    if (!card) return;

    const isTask = card.workflow_type === "task";
    const isClosed = card.status === "closed";
    const isReadonly = state.role === "chiki_piki";
    const hasAgentMessage = card.messages.some((m) => m.message_type === "agent");
    const hasAnyMessage = card.messages.length > 0;
    const greetedByMessage = card.messages.some((m) => m.message_type === "agent" && /^добр/i.test((m.body || "").trim()));
    const greeted = greetedByMessage || Boolean(card.greeting_used);
    const workflowType = card.workflow_type || "ticket";
    const isGreetingFlow = workflowType !== "task" && workflowType !== "auto";
    const showGreeting = !isReadonly && !isClosed && isGreetingFlow && greetingAllowed() && !greeted;
    const showClose = !isReadonly && !isClosed && (state.role === "admin" || (hasAnyMessage && hasAgentMessage));
    const showFreezeToggle = !isReadonly && !isClosed;
    const canRestore = state.role === "admin" || (card.closed_by_user_id && card.closed_by_user_id === state.userId);
    const canRestoreFlow = workflowType !== "task" && workflowType !== "auto";
    const showRestore = !isReadonly && isClosed && canRestoreFlow && canRestore;
    const isGroupChildTask = (card.task_kind || "") === "group_child" || Boolean(card.parent_ticket_id);
    const showWrongProblem = !isReadonly && !isClosed && !card.wrong_problem_used && !isGroupChildTask;
    const showMarkSpecial = state.role === "admin" && !isClosed && !card.is_special && workflowType === "ticket";
    const showAdminForceClose = state.role === "admin" && !isClosed;
    const showAdminDelete = state.role === "admin";
    const isCollecting = (card.intake_state || "ready") === "collecting";
    const showAutoCloseBox = !isReadonly && !isClosed && !card.is_special && workflowType === "ticket" && !isCollecting;

    setVisible(el.greetingBtn, showGreeting);
    setVisible(el.wrongProblemBtn, showWrongProblem);
    setVisible(el.closeBtn, showClose);
    if (el.adminForceCloseBtn) setVisible(el.adminForceCloseBtn, showAdminForceClose);
    if (el.adminDeleteBtn) setVisible(el.adminDeleteBtn, showAdminDelete);
    setVisible(el.freezeToggleBtn, showFreezeToggle);
    setVisible(el.restoreBtn, showRestore);
    if (el.markSpecialBtn) setVisible(el.markSpecialBtn, showMarkSpecial);
    if (el.autoCloseBox) setVisible(el.autoCloseBox, showAutoCloseBox);
    const canSeeInstruction = state.role !== "chiki_piki" && Boolean(card.instruction_text);
    if (el.instructionBtn) setVisible(el.instructionBtn, canSeeInstruction);
    setVisible(el.editClientBtn, !isReadonly);
    if (el.ticketAssigneeBox) el.ticketAssigneeBox.classList.toggle("hidden", state.role !== "admin");
    renderTicketAssigneeSelect(ctx);

    const isOnsiteTicket = workflowType === "ticket" && card.onsite_triggered;
    if (el.closeBtn) el.closeBtn.textContent = isTask ? "Закрыть задачу" : (isOnsiteTicket ? "Решил на месте" : "Закрыть заявку");
    if (el.adminForceCloseBtn) {
      el.adminForceCloseBtn.textContent = "Принудительно закрыть";
    }
    if (el.freezeToggleBtn) el.freezeToggleBtn.textContent = card.is_frozen ? "Разморозить" : "Заморозить";
    if (el.autoCloseDisabledCheckbox) {
      el.autoCloseDisabledCheckbox.checked = Boolean(card.auto_close_disabled);
    }
    if (el.autoCloseDueText) {
      const minutesLeft = minutesLeftToDue(card.auto_close_due_at);
      if (!showAutoCloseBox || card.auto_close_disabled || minutesLeft === null) {
        el.autoCloseDueText.textContent = "";
      } else {
        el.autoCloseDueText.textContent = `До закрытия: ${minutesLeft} м.`;
      }
    }
  }

  function bindTicketControlHandlers(ctx) {
    const { el, state, action, apiPost, apiPatch, loadCard, setStatus, showError, setVisible, openInstructionModal, closeInstructionModal, closeOnBackdropClick, assignCurrentTicket, saveClientProfile, openClientModal, closeClientModal } = ctx;

    if (el.greetingBtn) {
      el.greetingBtn.addEventListener("click", async (event) => {
        try {
          setStatus("Отправляю приветствие...");
          await apiPost(`/api/v1/tickets/${state.ticketId}/greeting`);
          if (state.card) state.card.greeting_used = true;
          setVisible(el.greetingBtn, false);
          await loadCard();
          setStatus("Приветствие отправлено");
        } catch (e) {
          showError(e.message);
          await loadCard({ silent: true });
        }
      });
    }

    if (el.freezeToggleBtn) {
      el.freezeToggleBtn.addEventListener("click", () => {
        const card = state.card;
        if (!card) return;
        if (card.is_frozen) {
          action("unfreeze");
          return;
        }
        const raw = el.freezeUntil.value;
        const freezeUntil = raw ? new Date(raw).toISOString() : null;
        action("freeze", { freeze_until: freezeUntil });
      });
    }

    if (el.closeBtn) el.closeBtn.addEventListener("click", () => action("close"));
    if (el.adminForceCloseBtn) el.adminForceCloseBtn.addEventListener("click", () => openAdminActionModal(ctx, "close"));
    if (el.adminDeleteBtn) el.adminDeleteBtn.addEventListener("click", () => openAdminActionModal(ctx, "delete"));
    if (el.restoreBtn) el.restoreBtn.addEventListener("click", () => action("restore"));

    if (el.autoCloseDisabledCheckbox) {
      el.autoCloseDisabledCheckbox.addEventListener("change", async () => {
        try {
          await apiPatch(`/api/v1/tickets/${state.ticketId}/auto-close`, {
            auto_close_disabled: Boolean(el.autoCloseDisabledCheckbox.checked),
          });
          await loadCard();
        } catch (e) {
          showError(e.message);
          el.autoCloseDisabledCheckbox.checked = Boolean(state.card?.auto_close_disabled);
        }
      });
    }

    if (el.wrongProblemBtn) el.wrongProblemBtn.addEventListener("click", () => openWrongProblemModal(ctx));
    if (el.instructionBtn) el.instructionBtn.addEventListener("click", openInstructionModal);
    if (el.closeInstructionModalBtn) el.closeInstructionModalBtn.addEventListener("click", closeInstructionModal);

    if (el.ticketAssigneeSelect) {
      el.ticketAssigneeSelect.addEventListener("change", async () => {
        await assignCurrentTicket(el.ticketAssigneeSelect.value || null);
      });
    }

    if (el.editClientBtn) el.editClientBtn.addEventListener("click", openClientModal);
    if (el.closeClientModalBtn) el.closeClientModalBtn.addEventListener("click", closeClientModal);
    if (el.closeWrongProblemModalBtn) el.closeWrongProblemModalBtn.addEventListener("click", () => closeWrongProblemModal(ctx));
    if (el.markSpecialBtn) el.markSpecialBtn.addEventListener("click", () => openSpecialConfirmModal(ctx));
    if (el.closeSpecialConfirmBtn) el.closeSpecialConfirmBtn.addEventListener("click", () => closeSpecialConfirmModal(ctx));
    if (el.confirmSpecialBtn) {
      el.confirmSpecialBtn.addEventListener("click", async () => {
        await markTicketSpecial(ctx);
      });
    }
    if (el.saveClientBtn) el.saveClientBtn.addEventListener("click", saveClientProfile);
    if (el.closeAdminActionModalBtn) el.closeAdminActionModalBtn.addEventListener("click", () => closeAdminActionModal(ctx));
    if (el.cancelAdminActionBtn) el.cancelAdminActionBtn.addEventListener("click", () => closeAdminActionModal(ctx));
    if (el.confirmAdminActionBtn) {
      el.confirmAdminActionBtn.addEventListener("click", async () => {
        try {
          await runAdminAction(ctx);
          closeAdminActionModal(ctx);
        } catch (e) {
          showError(e.message);
        }
      });
    }

    closeOnBackdropClick(el.clientModal, closeClientModal);
    closeOnBackdropClick(el.wrongProblemModal, () => closeWrongProblemModal(ctx));
    if (el.specialConfirmModal) closeOnBackdropClick(el.specialConfirmModal, () => closeSpecialConfirmModal(ctx));
    if (el.adminActionModal) closeOnBackdropClick(el.adminActionModal, () => closeAdminActionModal(ctx));
    if (el.instructionModal) closeOnBackdropClick(el.instructionModal, closeInstructionModal);
  }

  global.TicketControls = {
    renderTicketAssigneeSelect,
    assigneeOptionsHtml,
    renderWrongProblemRows,
    openWrongProblemModal,
    closeWrongProblemModal,
    chooseWrongProblem,
    openSpecialConfirmModal,
    closeSpecialConfirmModal,
    markTicketSpecial,
    openAdminActionModal,
    closeAdminActionModal,
    runAdminAction,
    renderTicketControlsView,
    bindTicketControlHandlers,
  };
})(window);
