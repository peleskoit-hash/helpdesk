(function () {
  const registry = new Map();
  const current = {
    name: "",
    root: null,
    destroy: null,
    url: "",
    initialized: false,
  };
  const settingsRoutes = new Map([
    ["/settings", { page: "settings-home", scripts: ["/static/settings_home.js?v=20260602a"] }],
    ["/settings/users", { page: "settings-users", scripts: ["/static/settings_users.js?v=20260603b"] }],
    ["/settings/client-card", { page: "settings-client-card", scripts: ["/static/settings_client_card.js?v=20260603b"] }],
    ["/settings/bot-buttons", {
      page: "settings-bot-buttons",
      scripts: [
        "/static/bot_buttons_api.js?v=20260520a",
        "/static/bot_buttons_files.js?v=20260520a",
        "/static/bot_buttons_level_view.js?v=20260520a",
        "/static/bot_buttons_editor.js?v=20260520a",
        "/static/bot_buttons_menu_view.js?v=20260521a",
        "/static/bot_buttons_editor_view.js?v=20260521a",
        "/static/settings_bot_buttons.js?v=20260602a",
      ],
    }],
  ]);
  const loadedScripts = new Set([...document.scripts].map((script) => script.getAttribute("src")).filter(Boolean));

  function getRoot() {
    return document.getElementById("appContent");
  }

  function normalizeDestroy(result) {
    if (typeof result === "function") return result;
    if (result && typeof result.destroy === "function") {
      return () => result.destroy();
    }
    return () => {};
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
      current.destroy = normalizeDestroy(factory({
        name,
        root,
        url: current.url,
      }));
      current.initialized = true;
    } else {
      current.destroy = () => {};
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

  function isSettingsRoute(url) {
    return settingsRoutes.has(url.pathname);
  }

  function canHandleUrl(url) {
    const currentUrl = new URL(window.location.href);
    return isSettingsRoute(currentUrl) && isSettingsRoute(url);
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
    return { link, url };
  }

  function updateSidebarActiveState(url) {
    document.querySelectorAll(".settings-link").forEach((link) => {
      const href = link.getAttribute("href") || "";
      const linkUrl = new URL(href, window.location.origin);
      const active = linkUrl.pathname === "/settings" && url.pathname.startsWith("/settings");
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
    const route = [...settingsRoutes.values()].find((item) => item.page === page);
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
      if (!settingsRoutes.get(url.pathname) || settingsRoutes.get(url.pathname).page !== payload.page) {
        throw new Error("Unexpected shell page");
      }
    } catch (error) {
      window.location.href = `${url.pathname}${url.search}${url.hash}`;
      return false;
    }

    try {
      if (!mountShellPayload(payload)) throw new Error("Shell mount failed");
      await ensurePageScript(payload.page);
      startCurrentPage();
    } catch (error) {
      renderShellError(error);
      return false;
    }

    updateSidebarActiveState(url);
    const nextUrl = `${url.pathname}${url.search}${url.hash}`;
    if (options.replace) {
      window.history.replaceState({ shell: true, url: nextUrl }, "", nextUrl);
    } else {
      window.history.pushState({ shell: true, url: nextUrl }, "", nextUrl);
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
    version: "20260603c",
  };

  document.addEventListener("click", (event) => {
    if (!isPlainLeftClick(event)) return;
    const match = findNavigableLink(event);
    if (!match) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    navigate(match.url);
  }, true);

  window.addEventListener("popstate", () => {
    const url = new URL(window.location.href);
    if (!canHandleUrl(url)) return;
    navigate(url, { replace: true });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startCurrentPage, { once: true });
  } else {
    startCurrentPage();
  }

  window.addEventListener("beforeunload", destroyCurrentPage);
})();
