const LEGACY_THEME_STORAGE_KEY = "hd_theme";
const THEME_PREFERENCES_URL = "/api/v1/users/me/preferences";

let themeToggleButton = null;
let themeToggleClickHandler = null;
let preferencesLoadPromise = null;
let currentTheme = "light";
let loadedTheme = "light";
let themeRevision = 0;
let confirmedTheme = "light";
let pendingThemeToSave = null;
let themeSaveInFlight = false;

function normalizeTheme(theme) {
  return theme === "dark" ? "dark" : "light";
}

function forgetLegacyTheme() {
  try {
    localStorage.removeItem(LEGACY_THEME_STORAGE_KEY);
  } catch {
    // localStorage can be unavailable in private or embedded contexts.
  }
}

function applyTheme(theme) {
  const normalized = normalizeTheme(theme);
  currentTheme = normalized;
  document.body.dataset.theme = normalized;

  document.querySelectorAll("#themeToggleBtn").forEach((button) => {
    button.textContent = normalized === "dark" ? "Светлая тема" : "Тёмная тема";
    button.setAttribute("aria-pressed", normalized === "dark" ? "true" : "false");
  });
}

async function fetchThemePreference() {
  const response = await fetch(THEME_PREFERENCES_URL, {
    headers: { accept: "application/json" },
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = await response.json();
  return normalizeTheme(payload?.theme_preference);
}

function loadThemePreference() {
  if (!preferencesLoadPromise) {
    const revisionAtRequest = themeRevision;
    preferencesLoadPromise = fetchThemePreference()
      .then((theme) => {
        loadedTheme = theme;
        confirmedTheme = theme;
        if (themeRevision === revisionAtRequest) {
          applyTheme(theme);
        }
        return theme;
      })
      .catch(() => {
        if (themeRevision === revisionAtRequest) {
          applyTheme(loadedTheme);
        }
        return loadedTheme;
      });
  }
  return preferencesLoadPromise;
}

async function patchThemePreference(theme) {
  const response = await fetch(THEME_PREFERENCES_URL, {
    method: "PATCH",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    credentials: "include",
    body: JSON.stringify({ theme_preference: theme }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = await response.json();
  return normalizeTheme(payload?.theme_preference);
}

async function flushThemeSaveQueue() {
  if (themeSaveInFlight) return;
  themeSaveInFlight = true;
  try {
    while (pendingThemeToSave) {
      const nextTheme = pendingThemeToSave;
      pendingThemeToSave = null;
      try {
        const savedTheme = await patchThemePreference(nextTheme);
        confirmedTheme = savedTheme;
        loadedTheme = savedTheme;
        if (!pendingThemeToSave) {
          applyTheme(savedTheme);
        }
      } catch {
        if (!pendingThemeToSave) {
          loadedTheme = confirmedTheme;
          applyTheme(confirmedTheme);
        }
      }
    }
  } finally {
    themeSaveInFlight = false;
  }
}

function saveThemePreference(nextTheme) {
  pendingThemeToSave = normalizeTheme(nextTheme);
  void flushThemeSaveQueue();
}

function initThemeToggle() {
  forgetLegacyTheme();
  applyTheme(currentTheme);
  void loadThemePreference();

  const button = document.getElementById("themeToggleBtn");
  if (themeToggleButton && themeToggleButton !== button && themeToggleClickHandler) {
    themeToggleButton.removeEventListener("click", themeToggleClickHandler);
  }
  if (button && button !== themeToggleButton) {
    themeToggleClickHandler = () => {
      const previousTheme = normalizeTheme(document.body.dataset.theme);
      const nextTheme = previousTheme === "dark" ? "light" : "dark";
      themeRevision += 1;
      applyTheme(nextTheme);
      saveThemePreference(nextTheme);
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
      localStorage.removeItem(LEGACY_THEME_STORAGE_KEY);
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
