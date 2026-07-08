const THEME_STORAGE_KEY = "hd_theme";
let themeToggleButton = null;
let themeToggleClickHandler = null;

function applyTheme(theme) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = normalized;
  localStorage.setItem(THEME_STORAGE_KEY, normalized);

  document.querySelectorAll("#themeToggleBtn").forEach((button) => {
    button.textContent = normalized === "dark" ? "Светлая тема" : "Тёмная тема";
    button.setAttribute("aria-pressed", normalized === "dark" ? "true" : "false");
  });
}

function initThemeToggle() {
  const savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "light";
  applyTheme(savedTheme);

  const button = document.getElementById("themeToggleBtn");
  if (themeToggleButton && themeToggleButton !== button && themeToggleClickHandler) {
    themeToggleButton.removeEventListener("click", themeToggleClickHandler);
  }
  if (button && button !== themeToggleButton) {
    themeToggleClickHandler = () => {
      const nextTheme = document.body.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
    };
    button.addEventListener("click", themeToggleClickHandler);
    themeToggleButton = button;
  }

  const logoutForms = document.querySelectorAll("form[action='/logout']");
  logoutForms.forEach((form) => {
    if (form.dataset.themeLogoutBound === "1") return;
    form.dataset.themeLogoutBound = "1";
    form.addEventListener("submit", () => {
      localStorage.removeItem("hd_role");
      localStorage.removeItem("hd_user_role");
      localStorage.removeItem("hd_user_id");
      localStorage.removeItem("hd_admin_assignee_ids");
      localStorage.removeItem("hd_tab_counts");
      localStorage.removeItem("hd_agents_cache");
      localStorage.removeItem("hd_completion_sidebar_open");
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initThemeToggle);
} else {
  initThemeToggle();
}

window.HelpdeskTheme = {
  applyTheme,
  initThemeToggle,
};
