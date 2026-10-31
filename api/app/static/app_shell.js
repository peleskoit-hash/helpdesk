(function () {
  const registry = new Map();
  const saveConfirmations = new Map();

  function showSaved(panel, options = {}) {
    if (!panel?.isConnected) return;
    saveConfirmations.get(panel)?.();
    const notice = document.createElement("div");
    notice.className = "save-confirmation";
    notice.textContent = "Сохранено";
    notice.setAttribute("role", "status");
    let timer;
    function cleanup() {
      clearTimeout(timer);
      notice.remove();
      window.removeEventListener("scroll", position, true);
      window.removeEventListener("resize", position);
      saveConfirmations.delete(panel);
    }
    function position() {
      if (!panel.isConnected) {
        cleanup();
        return;
      }
      const rect = panel.getBoundingClientRect();
      notice.style.left = `${rect.left + rect.width / 2}px`;
      notice.style.top = `${rect.top + rect.height / 2}px`;
      if (options.position === "top") {
        const scroller = options.scrollContainer || panel;
        const viewport = scroller.getBoundingClientRect();
        const right = viewport.left + scroller.clientLeft + scroller.clientWidth;
        const anchor = options.anchor?.getBoundingClientRect();
        notice.style.left = `${(rect.left + right) / 2}px`;
        notice.style.top = `${(anchor?.bottom ?? rect.top) + 8 + notice.offsetHeight / 2}px`;
      }
    }
    saveConfirmations.set(panel, cleanup);
    document.body.appendChild(notice);
    position();
    window.addEventListener("scroll", position, true);
    window.addEventListener("resize", position);
    timer = setTimeout(cleanup, 1000);
  }
  const current = {
    name: "",
    root: null,
    destroy: null,
    onUrlChange: null,
    canNavigateAway: null,
    url: "",
    initialized: false,
  };
  const shellRoutes = new Map([
    ["/reviews", { page: "reviews", scripts: ["/static/reviews.js?v=20260907a"] }],
    ["/management/reviews", { page: "reviews", scripts: ["/static/reviews.js?v=20260907a"] }],
    ["/management/feedback", { page: "management-feedback", scripts: ["/static/management_feedback.js?v=20260909b"] }],
    ["/management/statistics", { page: "management-statistics", scripts: ["/static/management_statistics.js?v=20261025-stats-final-v2"] }],
    ["/management/tasks", { page: "management-tasks", scripts: ["/static/desk_dialogs.js?v=20261027-dialog-prompts-v4", "/static/management_tasks.js?v=20261027-dialogs-complete-v3"] }],
    ["/management/saved-files", { page: "management-saved-files", scripts: ["/static/desk_dialogs.js?v=20261027-dialog-prompts-v4", "/static/management_saved_files.js?v=20261027-dialogs-complete-v3"] }],
    ["/settings", { page: "settings-home", scripts: ["/static/settings_home.js?v=20260602a"] }],
    ["/settings/users", { page: "settings-users", scripts: ["/static/settings_users.js?v=20261025-roles-editor-v2"] }],
    ["/settings/client-card", { page: "settings-client-card", scripts: ["/static/desk_dialogs.js?v=20261027-dialog-prompts-v4", "/static/settings_client_card.js?v=20261027-dialogs-complete-v3"] }],
    ["/settings/bot-buttons", {
      page: "settings-bot-buttons",
      scripts: [
        "/static/desk_dialogs.js?v=20261027-dialog-prompts-v4",
        "/static/bot_buttons_api.js?v=20260520a",
        "/static/bot_buttons_files.js?v=20261027-themed-dialogs-v1",
        "/static/bot_buttons_level_view.js?v=20261027-dialog-prompts-v4",
        "/static/telegram_text_editor.js?v=20261027-dialog-prompts-v4",
        "/static/bot_telegram_groups.js?v=20261021-group-copy",
        "/static/bot_buttons_editor.js?v=20261027-themed-dialogs-v1",
        "/static/bot_buttons_menu_view.js?v=20261027-themed-dialogs-v1",
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/bot_buttons_editor_view.js?v=20261027-dialog-prompts-v4",
        "/static/settings_bot_buttons.js?v=20261027-themed-dialogs-v1",
      ],
    }],
    ["/settings/automatic-messages", {
      page: "settings-automatic-messages",
      scripts: [
        "/static/desk_dialogs.js?v=20261027-dialog-prompts-v4",
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/settings_automatic_messages.js?v=20261027-dialog-prompts-v4",
      ],
    }],
    ["/settings/notification-bot-basic", {
      page: "settings-notification-bot-basic",
      scripts: [
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/telegram_text_editor.js?v=20261027-dialog-prompts-v4",
        "/static/desk_dialogs.js?v=20261027-dialog-prompts-v4",
        "/static/settings_notification_bot_basic.js?v=20261027-dialogs-complete-v3",
      ],
    }],
    ["/profile/notification-bot", {
      page: "profile-notification-bot",
      scripts: [
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/telegram_text_editor.js?v=20261027-dialog-prompts-v4",
        "/static/desk_dialogs.js?v=20261027-dialog-prompts-v4",
        "/static/profile_notification_bot.js?v=20261027-dialogs-complete-v3",
      ],
    }],
    ["/profile", { page: "profile", scripts: ["/static/profile.js?v=20261025-roles-editor-v2"] }],
    ["/statuses", { page: "statuses", scripts: ["/static/management_statistics.js?v=20261025-stats-final-v2", "/static/status_transitions.js?v=20261025-stats-final-v2", "/static/statuses.js?v=20261025-status-log-v2"] }],
    ["/mass-notifications", { page: "mass-notifications", scripts: ["/static/mass_notifications.js?v=20261013b"] }],
    ["/quick-replies", { page: "quick-replies", scripts: ["/static/desk_dialogs.js?v=20261027-dialog-prompts-v4", "/static/quick_replies.js?v=20261027-message-dialogs-v2"] }],
    ["/management", { page: "settings-home", scripts: ["/static/settings_home.js?v=20260602a"] }],
    ["/management/users", {
      page: "management-users",
      scripts: [
        "/static/client_web_credentials.js?v=20261018-web-empty-autofill",
        "/static/ticket_client_card.js?v=20261027-dialogs-complete-v3",
        "/static/desk_dialogs.js?v=20261027-dialog-prompts-v4",
        "/static/management_users.js?v=20261027-dialogs-complete-v3",
      ],
    }],
    ["/management/integrations", {
      page: "management-integrations",
      scripts: ["/static/management_integrations.js?v=20261025-status-log-v2"],
    }],
    ["/tickets", { page: "tickets-list", scripts: ["/static/desk_dialogs.js?v=20261027-dialog-prompts-v4", "/static/ticket_column_auto_width.js?v=20261027-list-columns-v3", "/static/list.js?v=20261029-ticket-history-v1"] }],
  ]);
  const dynamicShellRoutes = [
    {
      page: "ticket-card",
      test: (url) => /^\/tickets\/[^/]+$/.test(url.pathname),
      scripts: [
        "/static/desk_dialogs.js?v=20261027-dialog-prompts-v4",
        "/static/ticket_api.js?v=20260521a",
        "/static/ticket_attachments.js?v=20261031-web-history-v1",
        "/static/client_web_credentials.js?v=20261018-web-empty-autofill",
        "/static/ticket_client_card.js?v=20261027-dialogs-complete-v3",
        "/static/ticket_composer.js?v=20261029-staging-recovery-v1",
        "/static/ticket_controls.js?v=20261027-dialogs-complete-v3",
        "/static/ticket_message_view.js?v=20261027-dialogs-complete-v3",
        "/static/ticket.js?v=20261029-ticket-history-v1",
      ],
    },
  ];
  const loadedScripts = new Set([...document.scripts].map((script) => script.getAttribute("src")).filter(Boolean));
  let skipNextPopstateGuard = false;
  let skipNextPopstateGuardTimer = null;

  function clearSkippedPopstateGuard() {
    skipNextPopstateGuard = false;
    if (skipNextPopstateGuardTimer) {
      clearTimeout(skipNextPopstateGuardTimer);
      skipNextPopstateGuardTimer = null;
    }
  }

  function getRoot() {
    return document.getElementById("appContent");
  }

  function normalizeLifecycle(result) {
    if (typeof result === "function") return { destroy: result, onUrlChange: null, canNavigateAway: null };
    if (result && typeof result === "object") {
      return {
        destroy: typeof result.destroy === "function" ? () => result.destroy() : () => {},
        onUrlChange: typeof result.onUrlChange === "function" ? result.onUrlChange : null,
        canNavigateAway: typeof result.canNavigateAway === "function" ? result.canNavigateAway : null,
      };
    }
    return { destroy: () => {}, onUrlChange: null, canNavigateAway: null };
  }

  function confirmNavigateAway() {
    if (typeof current.canNavigateAway !== "function") return true;
    return current.canNavigateAway();
  }

  function destroyCurrentPage() {
    for (const cleanup of saveConfirmations.values()) cleanup();
    if (typeof current.destroy === "function") {
      try {
        current.destroy();
      } catch {
        // noop
      }
    }
    current.name = "";
    current.root = null;
    current.destroy = null;
    current.onUrlChange = null;
    current.canNavigateAway = null;
    current.url = "";
    current.initialized = false;
  }

  function startCurrentPage() {
    const root = getRoot();
    if (!root) return false;
    const name = String(root.dataset.shellPage || "").trim();
    if (!name) return false;
    if (current.root === root && current.name === name && current.initialized) {
      return true;
    }

    destroyCurrentPage();

    current.root = root;
    current.name = name;
    current.url = `${window.location.pathname}${window.location.search}`;

    const factory = registry.get(name);
    if (typeof factory === "function") {
      const lifecycle = normalizeLifecycle(factory({
        name,
        root,
        url: current.url,
      }));
      current.destroy = lifecycle.destroy;
      current.onUrlChange = lifecycle.onUrlChange;
      current.canNavigateAway = lifecycle.canNavigateAway;
      current.initialized = true;
    } else {
      current.destroy = () => {};
      current.onUrlChange = null;
      current.canNavigateAway = null;
      current.initialized = false;
    }
    return true;
  }

  function registerPage(name, factory) {
    if (!name || typeof factory !== "function") return;
    registry.set(String(name), factory);
    const root = getRoot();
    if (root && root.dataset.shellPage === String(name)) {
      startCurrentPage();
    }
  }

  function extractShellPayload(html) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(String(html || ""), "text/html");
    const root = doc.getElementById("appContent");
    if (!root) return null;
    return {
      title: doc.title || "",
      page: String(root.dataset.shellPage || "").trim(),
      html: root.innerHTML,
    };
  }

  function mountShellPayload(payload) {
    const root = getRoot();
    if (!root || !payload || !payload.page) return false;
    destroyCurrentPage();
    root.dataset.shellPage = payload.page;
    root.innerHTML = payload.html;
    if (payload.title) {
      document.title = payload.title;
    }
    return true;
  }

  function resolveShellRoute(url) {
    const exact = shellRoutes.get(url.pathname);
    if (exact) return exact;
    return dynamicShellRoutes.find((route) => route.test(url)) || null;
  }

  function isShellRoute(url) {
    return Boolean(resolveShellRoute(url));
  }

  function canHandleUrl(url) {
    const currentUrl = new URL(window.location.href);
    return isShellRoute(currentUrl) && isShellRoute(url);
  }

  function isPlainLeftClick(event) {
    return event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
  }

  function findNavigableLink(event) {
    const link = event.target?.closest?.("a[href]");
    if (!link) return null;
    if (link.target && link.target !== "_self") return null;
    if (link.hasAttribute("download")) return null;
    const url = new URL(link.href, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return null;
    if (!canHandleUrl(url)) return null;
    if (url.pathname === window.location.pathname && typeof current.onUrlChange === "function") return null;
    return { link, url };
  }

  function updateSidebarActiveState(url) {
    document.querySelectorAll(".settings-link").forEach((link) => {
      const href = link.getAttribute("href") || "";
      const linkUrl = new URL(href, window.location.origin);
      const active = linkUrl.pathname === "/settings"
        ? url.pathname.startsWith("/settings") || (url.pathname.startsWith("/management") && url.pathname !== "/management/tasks") || url.pathname === "/mass-notifications" || url.pathname === "/quick-replies"
        : linkUrl.pathname === "/management"
          ? (url.pathname.startsWith("/management") && url.pathname !== "/management/tasks") || url.pathname === "/mass-notifications"
        : linkUrl.pathname === url.pathname;
      link.classList.toggle("active", active);
    });
  }

  function renderShellError(error) {
    const message = error?.message || "ошибка загрузки страницы";
    window.HelpdeskErrorDialog?.show?.({
      title: "Ошибка загрузки страницы",
      message: "Не удалось открыть страницу внутри приложения. Технические подробности можно скопировать ниже.",
      error: message,
    });
  }

  async function ensurePageScript(page) {
    if (registry.has(page)) return;
    const route = [...shellRoutes.values(), ...dynamicShellRoutes].find((item) => item.page === page);
    if (!route?.scripts?.length) return;
    for (const src of route.scripts) {
      if (loadedScripts.has(src)) continue;
      await new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = src;
        script.onload = () => {
          loadedScripts.add(src);
          resolve();
        };
        script.onerror = () => reject(new Error(`Failed to load ${src}`));
        document.body.appendChild(script);
      });
    }
  }

  async function navigate(urlLike, options = {}) {
    const url = new URL(urlLike, window.location.origin);
    if (url.pathname === "/management") url.pathname = "/settings";
    if (!canHandleUrl(url)) {
      window.location.href = `${url.pathname}${url.search}${url.hash}`;
      return false;
    }
    if (options.skipGuard !== true && typeof current.canNavigateAway === "function" && !await current.canNavigateAway()) {
      return false;
    }
    let payload = null;
    try {
      const response = await fetch(`${url.pathname}${url.search}`, {
        headers: { accept: "text/html" },
        credentials: "include",
        cache: "no-store",
      });
      if (response.redirected && new URL(response.url).pathname === "/login") {
        throw new Error("сессия не передана при загрузке страницы");
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      payload = extractShellPayload(await response.text());
      if (!payload?.page) throw new Error("Shell content not found");
      const route = resolveShellRoute(url);
      if (!route || route.page !== payload.page) {
        throw new Error("Unexpected shell page");
      }
    } catch (error) {
      window.location.href = `${url.pathname}${url.search}${url.hash}`;
      return false;
    }

    try {
      if (!mountShellPayload(payload)) throw new Error("Shell mount failed");
      updateSidebarActiveState(url);
      const nextUrl = `${url.pathname}${url.search}${url.hash}`;
      if (options.replace) {
        window.history.replaceState({ shell: true, url: nextUrl }, "", nextUrl);
      } else {
        window.history.pushState({ shell: true, url: nextUrl }, "", nextUrl);
      }
      await ensurePageScript(payload.page);
      if (window.HelpdeskTheme?.initThemeToggle) {
        window.HelpdeskTheme.initThemeToggle();
      }
      startCurrentPage();
    } catch (error) {
      renderShellError(error);
      return false;
    }

    return true;
  }

  async function goBack() {
    if (!await confirmNavigateAway()) return false;
    skipNextPopstateGuard = true;
    if (skipNextPopstateGuardTimer) clearTimeout(skipNextPopstateGuardTimer);
    skipNextPopstateGuardTimer = setTimeout(clearSkippedPopstateGuard, 1000);
    window.history.back();
    return true;
  }

  window.HelpdeskAppShell = {
    showSaved,
    registerPage,
    navigate,
    goBack,
    startCurrentPage,
    destroyCurrentPage,
    confirmNavigateAway,
    canNavigateAway: confirmNavigateAway,
    extractShellPayload,
    mountShellPayload,
    getCurrentPage: () => ({ ...current }),
    version: "20260605c",
  };

  document.addEventListener("click", (event) => {
    const backButton = event.target?.closest?.("[data-shell-back]");
    if (backButton) {
      event.preventDefault();
      if (window.HelpdeskAppShell?.goBack) {
        window.HelpdeskAppShell.goBack();
      } else {
        window.history.back();
      }
      return;
    }
    if (!isPlainLeftClick(event)) return;
    const match = findNavigableLink(event);
    if (!match) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    navigate(match.url);
  }, true);

  window.addEventListener("popstate", async () => {
    const url = new URL(window.location.href);
    const skipGuard = skipNextPopstateGuard;
    clearSkippedPopstateGuard();
    if (url.pathname === current.url.split("?")[0] && typeof current.onUrlChange === "function") {
      await current.onUrlChange(url);
      current.url = `${url.pathname}${url.search}`;
      updateSidebarActiveState(url);
      return;
    }
    const previousUrl = current.url || `${window.location.pathname}${window.location.search}`;
    if (!skipGuard && !await confirmNavigateAway()) {
      window.history.pushState({ shell: true, url: previousUrl }, "", previousUrl);
      updateSidebarActiveState(new URL(previousUrl, window.location.origin));
      return;
    }
    if (!canHandleUrl(url)) return;
    await navigate(url, { replace: true, skipGuard: true });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startCurrentPage, { once: true });
  } else {
    startCurrentPage();
  }

  window.addEventListener("pagehide", (event) => {
    if (!event.persisted) destroyCurrentPage();
  });
})();
