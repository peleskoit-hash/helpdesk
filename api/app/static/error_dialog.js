(function () {
  function ensureDialog() {
    let modal = document.getElementById("appErrorDialog");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "appErrorDialog";
    modal.className = "modal hidden";
    modal.innerHTML = `
      <div class="modal-dialog">
        <div class="modal-head">
          <h3 id="appErrorDialogTitle">Ошибка загрузки страницы</h3>
          <button id="appErrorDialogCloseBtn" type="button" class="action">Закрыть</button>
        </div>
        <div class="modal-body">
          <p id="appErrorDialogMessage" class="status">Не удалось выполнить действие.</p>
          <textarea id="appErrorDialogDetails" class="error-textarea" readonly></textarea>
        </div>
        <div class="modal-actions">
          <button id="appErrorDialogCopyBtn" type="button" class="action">Скопировать</button>
          <button id="appErrorDialogOkBtn" type="button" class="action primary">Ок</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    const close = () => modal.classList.add("hidden");
    let startedOnBackdrop = false;
    modal.addEventListener("pointerdown", (event) => {
      startedOnBackdrop = event.target === modal;
    });
    modal.addEventListener("click", (event) => {
      if (startedOnBackdrop && event.target === modal) close();
      startedOnBackdrop = false;
    });
    modal.querySelector("#appErrorDialogCloseBtn")?.addEventListener("click", close);
    modal.querySelector("#appErrorDialogOkBtn")?.addEventListener("click", close);
    modal.querySelector("#appErrorDialogCopyBtn")?.addEventListener("click", async () => {
      const details = modal.querySelector("#appErrorDialogDetails");
      const text = details?.value || "";
      details?.select();
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        document.execCommand("copy");
      }
    });
    return modal;
  }

  function errorText(error) {
    if (typeof error === "string") return error;
    return error?.message || "Неизвестная ошибка";
  }

  function showErrorDialog({ title, message, error } = {}) {
    const modal = ensureDialog();
    const details = errorText(error);
    modal.querySelector("#appErrorDialogTitle").textContent = title || "Ошибка загрузки страницы";
    modal.querySelector("#appErrorDialogMessage").textContent = message || "Не удалось выполнить действие.";
    modal.querySelector("#appErrorDialogDetails").value = details;
    modal.classList.remove("hidden");
  }

  window.HelpdeskErrorDialog = {
    show: showErrorDialog,
  };
})();
