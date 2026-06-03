function initSettingsHomePage() {
  const statusText = document.getElementById("statusText");

  function setStatus(text) {
    if (statusText) statusText.textContent = text;
  }

  window.HelpdeskSidebar.init({
    onError: (e) => setStatus(`\u041e\u0448\u0438\u0431\u043a\u0430: ${e.message}`),
  })
    .then(() => setStatus("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u0430\u0437\u0434\u0435\u043b"))
    .catch(() => {});

  return () => {};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-home", initSettingsHomePage);
} else {
  initSettingsHomePage();
}
