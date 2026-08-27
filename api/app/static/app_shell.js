(function () {
  const registry = new Map();
  const current = {
    name: "",
    root: null,
    destroy: null,
    onUrlChange: null,
    url: "",
    initialized: false,
  };
  const shellRoutes = new Map([
    ["/settings", { page: "settings-home", scripts: ["/static/settings_home.js?v=20260602a"] }],
    ["/settings/users", { page: "settings-users", scripts: ["/static/settings_users.js?v=20260824a"] }],
    ["/settings/client-card", { page: "settings-client-card", scripts: ["/static/settings_client_card.js?v=20260803b"] }],
    ["/settings/bot-buttons", {
      page: "settings-bot-buttons",
      scripts: [
        "/static/bot_buttons_api.js?v=20260520a",
        "/static/bot_buttons_files.js?v=20260629a",
        "/static/bot_buttons_level_view.js?v=20260520a",
        "/static/telegram_text_editor.js?v=20260826a",
        "/static/bot_buttons_editor.js?v=20260629a",
        "/static/bot_buttons_menu_view.js?v=20260708a",
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/bot_buttons_editor_view.js?v=20260708a",
        "/static/settings_bot_buttons.js?v=20260708a",
      ],
    }],
    ["/settings/automatic-messages", {
      page: "settings-automatic-messages",
      scripts: [
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/settings_automatic_messages.js?v=20260605a",
      ],
    }],
    ["/settings/notification-bot-basic", {
      page: "settings-notification-bot-basic",
      scripts: [
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/telegram_text_editor.js?v=20260826a",
        "/static/settings_notification_bot_basic.js?v=20260826a",
      ],
    }],
    ["/profile/notification-bot", {
      page: "profile-notification-bot",
      scripts: [
        "/static/bot_buttons_emoji_data.js?v=20260605a",
        "/static/telegram_text_editor.js?v=20260826a",
        "/static/profile_notification_bot.js?v=20260826a",
      ],
    }],
    ["/statuses", { page: "statuses", scripts: ["/static/statuses.js?v=20260604a"] }],
    ["/mass-notifications", { page: "mass-notifications", scripts: ["/static/mass_notifications.js?v=20260604a"] }],
    ["/management", { page: "management-home", scripts: ["/static/management_home.js?v=20260709a"] }],
    ["/management/users", {
      page: "management-users",
      scripts: [
        "/static/ticket_client_card.js?v=20260803a",
        "/static/management_users.js?v=20260808c",
      ],
    }],
    ["/tickets", { page: "tickets-list", scripts: ["/static/list.js?v=20260604c"] }],
  ]);
  const dynamicShellRoutes = [
    {
      page: "ticket-card",
      test: (url) => /^\/tickets\/[^/]+$/.test(url.pathname),
      scripts: [
        "/static/ticket_api.js?v=20260521a",
        "/static/ticket_attachments.js?v=20260810b",
        "/static/ticket_client_card.js?v=20260803a",
        "/static/ticket_composer.js?v=20260824a",
        "/static/ticket_controls.js?v=20260810a",
        "/static/ticket_message_view.js?v=20260810b",
        "/static/ticket.js?v=20260824a",
      ],
    },
  ];
  const loadedScripts = new Set([...document.scripts].map((script) => script.getAttribute("src")).filter(Boolean));

  function getRoot() {
    return document.getElementById("appContent");
  }

  function normalizeLifecycle(result) {
    if (typeof result === "function") return { destroy: result, onUrlChange: null };
    if (result && typeof result === "object") {
      return {
        destroy: typeof result.destroy === "function" ? () => result.destroy() : () => {},
        onUrlChange: typeof result.onUrlChange === "function" ? result.onUrlChange : null,
      };
    }
    return { destroy: () => {}, onUrlChange: null };
  }

  function destroyCurrentPage() {
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
      current.initialized = true;
    } else {
      current.destroy = () => {};
      current.onUrlChange = null;
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
        ? url.pathname.startsWith("/settings")
        : linkUrl.pathname === "/management"
          ? url.pathname.startsWith("/management") || url.pathname === "/mass-notifications"
        : linkUrl.pathname === url.pathname;
      link.classList.toggle("active", active);
    });
  }

  function renderShellError(error) {
    const status = document.getElementById("statusText");
    const message = error?.message || "ошибка загрузки страницы";
    if (status) {
      status.textContent = `Ошибка: ${message}`;
    }
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
    if (!canHandleUrl(url)) {
      window.location.href = `${url.pathname}${url.search}${url.hash}`;
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

  window.HelpdeskAppShell = {
    registerPage,
    navigate,
    startCurrentPage,
    destroyCurrentPage,
    extractShellPayload,
    mountShellPayload,
    getCurrentPage: () => ({ ...current }),
    version: "20260605c",
  };

  document.addEventListener("click", (event) => {
    if (!isPlainLeftClick(event)) return;
    const match = findNavigableLink(event);
    if (!match) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    navigate(match.url);
  }, true);

  window.addEventListener("popstate", async () => {
    const url = new URL(window.location.href);
    if (!canHandleUrl(url)) return;
    if (url.pathname === current.url.split("?")[0] && typeof current.onUrlChange === "function") {
      await current.onUrlChange(url);
      current.url = `${url.pathname}${url.search}`;
      updateSidebarActiveState(url);
      return;
    }
    navigate(url, { replace: true });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startCurrentPage, { once: true });
  } else {
    startCurrentPage();
  }

  window.addEventListener("beforeunload", destroyCurrentPage);
})();
