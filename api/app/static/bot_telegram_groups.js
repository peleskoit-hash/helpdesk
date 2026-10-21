(function (global) {
  const Groups = {};
  const endpoint = "/api/v1/settings/telegram-groups";

  Groups.load = async function (ctx) {
    ctx.state.telegramGroups = (await ctx.api(endpoint)).items || [];
  };

  Groups.html = function (ctx, escapeHtml) {
    const { state } = ctx;
    if (state.automationTypeValue !== "telegram_group") return "";
    const groups = state.telegramGroups || [];
    const selected = groups.find(group => group.id === state.telegramGroupId);
    const command = state.telegramGroupCommand;
    return `<div class="bot-automation-settings">
      <label class="field-label" for="botTelegramGroup">Telegram-группа</label>
      <select id="botTelegramGroup" class="input">
        <option value="">Выберите проверенную группу</option>
        ${!selected && state.telegramGroupId ? `<option value="${escapeHtml(state.telegramGroupId)}" selected disabled>Группа недоступна</option>` : ""}
        ${groups.map(group => `<option value="${escapeHtml(group.id)}" ${group.id === state.telegramGroupId ? "selected" : ""} ${group.status === "verified" ? "" : "disabled"}>${escapeHtml(group.title)} (${escapeHtml(group.chat_id)}) — ${group.status === "verified" ? "Проверена" : "Нет доступа"}</option>`).join("")}
      </select>
      <div class="status">Пересылка пока не реализована. Обращение будет передано сотруднику.</div>
      ${selected?.last_error ? `<div class="status">${selected.last_error === "probe_cleanup_failed" ? "Проверка отправки успешна, но тестовое сообщение не удалось удалить." : "Проверьте доступ бота к группе повторно."}</div>` : ""}
      <button id="botTelegramGroupAdd" type="button" class="action">Добавить группу</button>
      <button id="botTelegramGroupRefresh" type="button" class="action">Обновить список</button>
      <button id="botTelegramGroupCheck" type="button" class="action" ${selected ? "" : "disabled"}>Проверить доступ</button>
      ${command ? `<div class="status">Добавьте клиентского бота в тестовую группу. Администратор группы должен отправить команду:<br><code>${escapeHtml(command.command)}</code><br>Действует до ${escapeHtml(command.expires_at)}. После регистрации обновите список.</div>` : ""}
    </div>`;
  };

  Groups.bind = function (ctx) {
    if (ctx.state.automationTypeValue !== "telegram_group") return;
    const { state } = ctx;
    document.getElementById("botTelegramGroup")?.addEventListener("change", event => {
      state.telegramGroupId = event.target.value;
      global.BotButtonsEditorView.renderEditor(ctx);
    });
    const action = (id, perform) => document.getElementById(id)?.addEventListener("click", async event => {
      const leafKey = state.selectedLeaf?.technical_key;
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await perform();
        if (state.selectedLeaf?.technical_key === leafKey && state.automationTypeValue === "telegram_group") {
          global.BotButtonsEditorView.renderEditor(ctx);
        }
      } catch (_) {
        ctx.setStatus("Не удалось выполнить действие с Telegram-группой");
      } finally {
        button.disabled = false;
      }
    });
    action("botTelegramGroupAdd", async () => {
      state.telegramGroupCommand = await ctx.api(`${endpoint}/registration-code`, { method: "POST" });
    });
    action("botTelegramGroupRefresh", () => Groups.load(ctx));
    action("botTelegramGroupCheck", async () => {
      if (!state.telegramGroupId) return;
      await ctx.api(`${endpoint}/${encodeURIComponent(state.telegramGroupId)}/check`, { method: "POST" });
      await Groups.load(ctx);
    });
  };

  global.BotTelegramGroups = Groups;
})(window);
