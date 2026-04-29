const THEME_STORAGE_KEY = "hd_theme";

function applyTheme(theme) {
  const normalized = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = normalized;
  localStorage.setItem(THEME_STORAGE_KEY, normalized);

  const button = document.getElementById("themeToggleBtn");
  if (button) {
    button.textContent = normalized === "dark" ? "Светлая тема" : "Тёмная тема";
    button.setAttribute("aria-pressed", normalized === "dark" ? "true" : "false");
  }
}

function initThemeToggle() {
  const savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "light";
  applyTheme(savedTheme);

  const button = document.getElementById("themeToggleBtn");
  if (!button) return;

  button.addEventListener("click", () => {
    const nextTheme = document.body.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initThemeToggle);
} else {
  initThemeToggle();
}
