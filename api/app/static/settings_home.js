function initSettingsHomePage() {
  window.HelpdeskSidebar.init({
    onError: (error) => window.HelpdeskErrorDialog?.show?.({
      title: "Ошибка загрузки страницы",
      message: "Не удалось обновить данные боковой панели.",
      error,
    }),
  }).catch(() => {});

  return () => {};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-home", initSettingsHomePage);
} else {
  initSettingsHomePage();
}