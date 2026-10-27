(function () {
  if (window.HelpdeskDialogs) return;
  let queue = Promise.resolve();
  function show(message, confirm, options, prompt = false, defaultValue = "") {
    const result = queue.then(() => new Promise((resolve) => {
      const previous = document.activeElement;
      const dialog = document.createElement("dialog");
      dialog.className = "desk-dialog";
      dialog.setAttribute("aria-labelledby", "deskDialogTitle");
      dialog.setAttribute("aria-describedby", "deskDialogMessage");
      dialog.innerHTML = '<h2 id="deskDialogTitle"></h2><div id="deskDialogMessage" class="desk-dialog-message"></div><div class="desk-dialog-actions"><button type="button" data-dialog-cancel></button><button type="button" data-dialog-accept></button></div>';
      dialog.querySelector("h2").textContent = options.title || (prompt ? "Ввод данных" : confirm ? "Подтверждение" : "Сообщение");
      dialog.querySelector(".desk-dialog-message").textContent = String(message ?? "");
      const cancel = dialog.querySelector("[data-dialog-cancel]");
      const accept = dialog.querySelector("[data-dialog-accept]");
      cancel.textContent = options.cancelLabel || "Отмена";
      let input = null;
      if (prompt) {
        input = document.createElement("input");
        input.type = "text";
        input.className = "desk-dialog-input";
        input.setAttribute("aria-labelledby", "deskDialogMessage");
        input.value = String(defaultValue);
        dialog.insertBefore(input, dialog.querySelector(".desk-dialog-actions"));
      }
      cancel.hidden = !confirm && !prompt;
      accept.textContent = options.acceptLabel || (confirm || prompt ? "Подтвердить" : "ОК");
      let finished = false;
      const finish = (accepted) => {
        if (finished) return;
        finished = true;
        window.removeEventListener("keydown", onKey, true);
        dialog.close();
        dialog.remove();
        if (previous?.isConnected) previous.focus({ preventScroll: true });
        resolve(prompt ? (accepted ? input.value : null) : accepted);
      };
      const onKey = (event) => {
        if (event.key === "Tab") {
          event.preventDefault();
          event.stopImmediatePropagation();
          const buttons = prompt ? [input, cancel, accept] : confirm ? [cancel, accept] : [accept];
          const index = buttons.indexOf(document.activeElement);
          buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
          return;
        }
        if (prompt && event.key === "Enter" && document.activeElement === input && !event.isComposing) {
          event.preventDefault();
          event.stopImmediatePropagation();
          finish(true);
          return;
        }
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopImmediatePropagation();
        finish(false);
      };
      cancel.addEventListener("click", () => finish(false));
      accept.addEventListener("click", () => finish(true));
      dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
      document.body.appendChild(dialog);
      window.addEventListener("keydown", onKey, true);
      dialog.showModal();
      (prompt ? input : confirm ? cancel : accept).focus();
      if (prompt) input.select();
    }));
    queue = result.catch(() => {});
    return result;
  }
  window.HelpdeskDialogs = {
    confirm: (message, options = {}) => show(message, true, options),
    alert: (message, options = {}) => show(message, false, options),
    prompt: (message, defaultValue = "") => show(message, true, {}, true, defaultValue),
  };
})();
