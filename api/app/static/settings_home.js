const el = {
  statusText: document.getElementById("statusText"),
};

function setStatus(text) {
  if (el.statusText) el.statusText.textContent = text;
}

(function init() {
  window.HelpdeskSidebar.init({
    onError: (e) => setStatus(`Ошибка: ${e.message}`),
  })
    .then(() => setStatus("Выберите раздел"))
    .catch(() => {});
})();
