const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class EventTargetLike {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  removeEventListener(type, handler) {
    const list = this.listeners.get(type) || [];
    this.listeners.set(type, list.filter((item) => item !== handler));
  }

  dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    event.defaultPrevented = false;
    event.preventDefault ||= () => {
      event.defaultPrevented = true;
    };
    const list = this.listeners.get(event.type) || [];
    const results = list.map((handler) => handler(event));
    this.lastDispatchPromises = results.filter((result) => result && typeof result.then === "function");
    return !event.defaultPrevented;
  }
}

class ClassListLike {
  constructor(element) {
    this.element = element;
    this.classes = new Set();
  }

  setFromString(value) {
    this.classes = new Set(String(value || "").split(/\s+/).filter(Boolean));
  }

  add(...names) {
    names.forEach((name) => this.classes.add(name));
    this.#sync();
  }

  remove(...names) {
    names.forEach((name) => this.classes.delete(name));
    this.#sync();
  }

  contains(name) {
    return this.classes.has(name);
  }

  toggle(name, force) {
    if (force === true) {
      this.classes.add(name);
    } else if (force === false) {
      this.classes.delete(name);
    } else if (this.classes.has(name)) {
      this.classes.delete(name);
    } else {
      this.classes.add(name);
    }
    this.#sync();
    return this.classes.has(name);
  }

  toString() {
    return [...this.classes].join(" ");
  }

  #sync() {
    this.element.attributes.class = this.toString();
  }
}

class ElementLike extends EventTargetLike {
  constructor(tagName, ownerDocument) {
    super();
    this.ownerDocument = ownerDocument;
    this.tagName = String(tagName || "").toUpperCase();
    this.attributes = {};
    this.dataset = {};
    this.children = [];
    this.parentNode = null;
    this.hidden = false;
    this.textContent = "";
    this.classList = new ClassListLike(this);
  }

  get className() {
    return this.attributes.class || "";
  }

  set className(value) {
    this.classList.setFromString(value);
  }

  get id() {
    return this.attributes.id || "";
  }

  set id(value) {
    this.attributes.id = String(value);
  }

  get nextSibling() {
    if (!this.parentNode) return null;
    const siblings = this.parentNode.children;
    const index = siblings.indexOf(this);
    return index === -1 ? null : siblings[index + 1] || null;
  }

  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
    }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  insertBefore(child, referenceNode) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
    }
    child.parentNode = this;
    if (!referenceNode) {
      this.children.push(child);
      return child;
    }
    const index = this.children.indexOf(referenceNode);
    if (index === -1) {
      this.children.push(child);
      return child;
    }
    this.children.splice(index, 0, child);
    return child;
  }

  replaceWith(nextNode) {
    if (!this.parentNode) return;
    const siblings = this.parentNode.children;
    const index = siblings.indexOf(this);
    if (index === -1) return;
    nextNode.parentNode = this.parentNode;
    siblings.splice(index, 1, nextNode);
    this.parentNode = null;
  }

  contains(node) {
    if (!node) return false;
    if (node === this) return true;
    return this.children.some((child) => child.contains(node));
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  setAttribute(name, value) {
    const rawName = String(name);
    const normalized = rawName.toLowerCase();
    const stringValue = String(value);
    this.attributes[normalized] = stringValue;
    if (normalized === "class") {
      this.classList.setFromString(stringValue);
    } else if (normalized === "id") {
      this.attributes.id = stringValue;
    } else if (normalized.startsWith("data-")) {
      const key = normalized.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
      this.dataset[key] = stringValue;
    }
  }

  getAttribute(name) {
    return this.attributes[String(name).toLowerCase()] ?? null;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    return findMatches(this, selector);
  }

  requestSubmit() {
    this.requestSubmitCalls = (this.requestSubmitCalls || 0) + 1;
  }

  submit() {
    this.submitCalls = (this.submitCalls || 0) + 1;
  }
}

class DocumentLike extends EventTargetLike {
  constructor() {
    super();
    this.readyState = "complete";
    this.activeElement = null;
    this.body = new ElementLike("body", this);
  }

  createElement(tagName) {
    return new ElementLike(tagName, this);
  }

  querySelector(selector) {
    return this.body.querySelector(selector);
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }

  getElementById(id) {
    return this.querySelector(`#${id}`);
  }
}

function findMatches(root, selector) {
  const parts = String(selector).trim().split(/\s+/);
  const results = [];
  walk(root, (node) => {
    if (!(node instanceof ElementLike)) return;
    if (!matchesSelectorChain(node, parts)) return;
    results.push(node);
  });
  return results;
}

function walk(node, visitor) {
  node.children.forEach((child) => {
    visitor(child);
    walk(child, visitor);
  });
}

function matchesSelectorChain(node, parts) {
  if (!matchesSimpleSelector(node, parts[parts.length - 1])) return false;
  let current = node.parentNode;
  for (let index = parts.length - 2; index >= 0; index -= 1) {
    while (current && !matchesSimpleSelector(current, parts[index])) {
      current = current.parentNode;
    }
    if (!current) return false;
    current = current.parentNode;
  }
  return true;
}

function matchesSimpleSelector(node, selector) {
  const source = String(selector);
  const tagMatch = source.match(/^[a-zA-Z]+/);
  if (tagMatch && node.tagName !== tagMatch[0].toUpperCase()) return false;

  const idMatch = source.match(/#([a-zA-Z0-9_-]+)/);
  if (idMatch && node.id !== idMatch[1]) return false;

  const classMatches = [...source.matchAll(/\.([a-zA-Z0-9_-]+)/g)];
  if (classMatches.some((match) => !node.classList.contains(match[1]))) return false;

  const attrMatches = [...source.matchAll(/\[([a-zA-Z0-9_:-]+)(?:=['"]?([^'"\]]+)['"]?)?\]/g)];
  return attrMatches.every((match) => {
    const attrName = match[1];
    const expected = match[2];
    const actual = node.getAttribute(attrName);
    const datasetKey = attrName.replace(/^data-/, "").replace(/-([a-z])/g, (_, char) => char.toUpperCase());
    const datasetValue = Object.hasOwn(node.dataset, datasetKey) ? node.dataset[datasetKey] : null;
    if (expected == null) return actual !== null || datasetValue !== null;
    if (actual === expected) return true;
    if (datasetValue === expected) return true;
    return actual === expected;
  });
}

function createElement(document, tagName, options = {}) {
  const node = document.createElement(tagName);
  if (options.id) node.id = options.id;
  if (options.className) node.className = options.className;
  if (options.textContent != null) node.textContent = options.textContent;
  if (options.hidden) node.hidden = true;
  if (options.attributes) {
    Object.entries(options.attributes).forEach(([name, value]) => node.setAttribute(name, value));
  }
  return node;
}

const STATUS_DEFINITIONS = [
  ["offline", "status-offline", "Не в сети"],
  ["online", "status-online", "В сети"],
  ["on_ticket", "status-on-ticket", "На заявке"],
  ["waiting", "status-waiting", "В ожидании"],
  ["lunch", "status-lunch", "Обед"],
];

function createStatusPanel(document, withButtons = true) {
  const statusPanel = createElement(document, "div", { className: "status-panel hidden" });
  if (!withButtons) return statusPanel;
  statusPanel.appendChild(createElement(document, "div", { className: "status-panel-title", textContent: "Статус" }));
  for (const [status, className, label] of STATUS_DEFINITIONS) {
    statusPanel.appendChild(createElement(document, "button", {
      className: `agent-status-btn ${className}`,
      textContent: label,
      attributes: { "data-availability-status": status },
    }));
  }
  return statusPanel;
}

function buildSidebarDom({ statusPanel = "empty" } = {}) {
  const document = new DocumentLike();
  const aside = createElement(document, "aside", { className: "sidebar" });
  const massLink = createElement(document, "a", {
    className: "settings-link",
    attributes: { href: "/mass-notifications" },
  });
  const stats = createElement(document, "div", { id: "sidebarUserStats", className: "sidebar-user-stats" });
  const statsRow = createElement(document, "div", { className: "sidebar-user-name-row" });
  const legacyName = createElement(document, "div", {
    id: "sidebarUserDisplayName",
    className: "sidebar-user-name",
    textContent: "—",
  });
  const completionToggle = createElement(document, "button", {
    id: "completionToggleBtn",
    className: "completion-toggle-icon hidden",
  });
  const completionDetails = createElement(document, "div", {
    id: "completionDetails",
    className: "completion-details hidden",
  });
  statsRow.appendChild(legacyName);
  statsRow.appendChild(completionToggle);
  stats.appendChild(statsRow);
  stats.appendChild(completionDetails);

  const sidebarBottom = createElement(document, "div", { className: "sidebar-bottom" });
  const userActions = createElement(document, "div", { className: "sidebar-user-actions" });
  const avatar = createElement(document, "div", {
    className: "agent-avatar",
    textContent: "A",
    attributes: { title: "Пользователь" },
  });
  const logoutForm = createElement(document, "form", {
    attributes: { action: "/logout", method: "post" },
  });
  userActions.appendChild(avatar);
  userActions.appendChild(logoutForm);
  if (statusPanel === "empty") {
    sidebarBottom.appendChild(createStatusPanel(document, false));
  } else if (statusPanel === "standard") {
    sidebarBottom.appendChild(createStatusPanel(document, true));
  }
  sidebarBottom.appendChild(userActions);

  const outside = createElement(document, "div", { id: "outside" });
  aside.appendChild(massLink);
  aside.appendChild(stats);
  aside.appendChild(sidebarBottom);
  document.body.appendChild(aside);
  document.body.appendChild(outside);
  return { document, aside, stats, logoutForm, outside };
}

function createFetchMock(role = "agent") {
  const calls = [];
  const fetchMock = async (path, options = {}) => {
    calls.push({ path: String(path), method: options.method || "GET", body: options.body || "" });
    let payload = {};
    if (path === "/api/v1/auth/session") {
      payload = {
        real_user: { id: "u1", role, login: "ivan", full_name: "Ivan Petrov", availability_status: "waiting" },
        effective_user: { id: "u1", role, login: "ivan", full_name: "Ivan Petrov", availability_status: "waiting" },
        is_real_admin: role === "admin",
        session_login_ts: 1,
        app_timezone: "Asia/Tokyo",
      };
    } else if (path === "/api/v1/users/agents") {
      payload = [{ id: "u1", full_name: "Ivan Petrov" }];
    } else if (String(path).startsWith("/api/v1/tickets/counts")) {
      payload = { unassigned: 0, active: 1, special: 0, frozen: 0, closed: 0 };
    } else if (path === "/api/v1/users/me/status") {
      payload = { availability_status: JSON.parse(options.body || "{}").availability_status || "offline" };
    }
    return {
      ok: true,
      text: async () => JSON.stringify(payload),
    };
  };
  fetchMock.calls = calls;
  return fetchMock;
}

async function loadSidebar(document, role = "agent") {
  const source = fs.readFileSync("api/app/static/sidebar.js", "utf8");
  const window = {
    location: { pathname: "/tickets", href: "/tickets" },
  };
  const localStorage = new Map();
  const storageApi = {
    getItem(key) {
      return localStorage.has(key) ? localStorage.get(key) : null;
    },
    setItem(key, value) {
      localStorage.set(key, String(value));
    },
    removeItem(key) {
      localStorage.delete(key);
    },
  };
  const fetchMock = createFetchMock(role);
  window.__fetchCalls = fetchMock.calls;
  const context = vm.createContext({
    window,
    document,
    console: { info() {}, error() {}, log() {} },
    localStorage: storageApi,
    fetch: fetchMock,
    URLSearchParams,
    JSON,
    Array,
    Set,
    Map,
    String,
    Object,
    Number,
    Boolean,
    Error,
  });
  vm.runInContext(source, context);
  await window.HelpdeskSidebar.init();
  return window;
}

test("sidebar user menu init creates separate panel and binds interactions", async () => {
  const { document, aside, stats, logoutForm, outside } = buildSidebarDom();
  const window = await loadSidebar(document);

  assert.ok(window.HelpdeskSidebar);
  assert.equal((await window.HelpdeskSidebar.init()).appTimezone, "Asia/Tokyo");
  assert.equal(window.HelpdeskSidebar.getState().appTimezone, "Asia/Tokyo");
  const panel = document.querySelector("[data-sidebar-user-panel]");
  assert.ok(panel);
  assert.equal(panel.parentNode.classList.contains("sidebar-bottom"), true);
  assert.equal(stats.parentNode, aside);
  assert.equal(panel.contains(stats), false);

  const visibleName = document.querySelector("[data-sidebar-user-full-name]");
  assert.equal(visibleName.textContent, "Ivan Petrov");

  const legacyName = document.getElementById("sidebarUserDisplayName");
  assert.ok(legacyName);
  assert.ok(legacyName.hidden);
  assert.equal(stats.contains(legacyName), true);

  const avatarButton = document.querySelector("[data-sidebar-user-menu-toggle]");
  assert.equal(avatarButton.tagName, "BUTTON");
  assert.equal(avatarButton.getAttribute("aria-expanded"), "false");

  const menu = document.querySelector("[data-sidebar-user-menu]");
  assert.ok(menu.hidden);
  const menuItems = menu.querySelectorAll("[data-sidebar-user-menu-item]");
  assert.deepEqual(menuItems.map((item) => item.textContent), [
    "Личные данные",
    "Уведомления в боте",
    "Отзывы",
    "Выход",
  ]);

  avatarButton.dispatchEvent({ type: "click", target: avatarButton });
  assert.equal(menu.hidden, false);
  assert.equal(avatarButton.getAttribute("aria-expanded"), "true");

  avatarButton.dispatchEvent({ type: "click", target: avatarButton });
  assert.equal(menu.hidden, true);
  assert.equal(avatarButton.getAttribute("aria-expanded"), "false");

  avatarButton.dispatchEvent({ type: "keydown", key: "Enter", target: avatarButton });
  assert.equal(menu.hidden, false);
  document.dispatchEvent({ type: "keydown", key: "Escape", target: document.body });
  assert.equal(menu.hidden, true);

  avatarButton.dispatchEvent({ type: "click", target: avatarButton });
  document.dispatchEvent({ type: "pointerdown", target: outside });
  assert.equal(menu.hidden, true);

  avatarButton.dispatchEvent({ type: "click", target: avatarButton });
  const logoutItem = menu.querySelector('[data-sidebar-user-menu-item="logout"]');
  logoutItem.dispatchEvent({ type: "click", target: logoutItem });
  assert.equal(logoutForm.requestSubmitCalls, 1);
  assert.equal(menu.hidden, true);

  await window.HelpdeskSidebar.init();
  assert.equal(document.querySelectorAll("[data-sidebar-user-menu]").length, 1);
});

test("profile user menu entry navigates to profile page", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document);
  const menu = document.querySelector("[data-sidebar-user-menu]");
  const item = menu.querySelector('[data-sidebar-user-menu-item="profile"]');
  const navigations = [];
  window.HelpdeskAppShell = { navigate: (url) => navigations.push(url) };

  item.dispatchEvent({ type: "click", target: item });

  assert.deepEqual(navigations, ["/profile"]);
  assert.equal(menu.hidden, true);
});

test("sidebar rehydrates missing status panel on repeated init without extra session fetch", async () => {
  const { document } = buildSidebarDom({ statusPanel: "none" });
  const window = await loadSidebar(document, "agent");
  const panel = document.querySelector(".status-panel");

  assert.ok(panel);
  assert.equal(panel.parentNode.classList.contains("sidebar-bottom"), true);
  assert.equal(panel.classList.contains("hidden"), false);
  assert.deepEqual(
    panel.querySelectorAll("[data-availability-status]").map((button) => button.dataset.availabilityStatus),
    ["offline", "online", "on_ticket", "waiting", "lunch"],
  );
  assert.equal(panel.querySelector("[data-availability-status='waiting']").classList.contains("active"), true);
  const callsAfterFirstInit = window.__fetchCalls.length;

  await window.HelpdeskSidebar.init();

  assert.equal(window.__fetchCalls.length, callsAfterFirstInit);
  assert.equal(window.__fetchCalls.filter((call) => call.path === "/api/v1/auth/session").length, 1);
  assert.equal(document.querySelectorAll(".status-panel").length, 1);
  assert.equal(document.querySelectorAll("[data-availability-status]").length, 5);
});

test("sidebar reuses existing status panel and repeated init keeps one click handler", async () => {
  const { document } = buildSidebarDom({ statusPanel: "standard" });
  const window = await loadSidebar(document, "agent");
  const callsAfterFirstInit = window.__fetchCalls.length;

  await window.HelpdeskSidebar.init();
  await window.HelpdeskSidebar.init();

  assert.equal(window.__fetchCalls.length, callsAfterFirstInit);
  assert.equal(document.querySelectorAll(".status-panel").length, 1);
  assert.equal(document.querySelectorAll("[data-availability-status]").length, 5);

  const onlineButton = document.querySelector("[data-availability-status='online']");
  onlineButton.dispatchEvent({ type: "click", target: onlineButton });
  await Promise.all(onlineButton.lastDispatchPromises || []);

  assert.equal(window.__fetchCalls.filter((call) => call.path === "/api/v1/users/me/status").length, 1);
  assert.equal(onlineButton.classList.contains("active"), true);
});

test("sidebar status panel stays hidden for admin", async () => {
  const { document } = buildSidebarDom({ statusPanel: "none" });
  await loadSidebar(document, "admin");

  const panel = document.querySelector(".status-panel");
  assert.ok(panel);
  assert.equal(panel.classList.contains("hidden"), true);
  assert.equal(document.querySelectorAll("[data-availability-status]").length, 5);
});

for (const role of ["admin", "agent", "trainee", "chiki_piki"]) {
  test("review profile entry follows role: " + role, async () => {
    const { document } = buildSidebarDom();
    const window = await loadSidebar(document, role);
    const item = document.querySelector('[data-sidebar-user-menu-item="feedback"]');
    const allowed = role === "agent" || role === "trainee";
    assert.equal(item.hidden, !allowed);
    item.dispatchEvent({ type: "click", target: item });
    assert.equal(window.location.href, allowed ? "/reviews" : "/tickets");
    const navigations = [];
    window.HelpdeskAppShell = { navigate: (url) => navigations.push(url) };
    item.dispatchEvent({ type: "click", target: item });
    assert.deepEqual(navigations, allowed ? ["/reviews"] : []);
  });
}
