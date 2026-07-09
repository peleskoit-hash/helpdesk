(function () {
  async function initManagementHomePage() {
    await window.HelpdeskSidebar?.init?.();
  }

  window.HelpdeskAppShell?.registerPage?.("management-home", initManagementHomePage);
})();
