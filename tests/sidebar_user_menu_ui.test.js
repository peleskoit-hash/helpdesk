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
    this.style = {};
    this.classList = new ClassListLike(this);
  }

  get className() {
    return this.attributes.class || "";
  }

  set className(value) {
    this.classList.setFromString(value);
  }

  set innerHTML(value) {
    this.children = [];
    this.textContent = String(value || "");
  }

  get innerHTML() {
    return this.textContent;
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

  closest(selector) {
    let current = this;
    while (current) {
      if (matchesSimpleSelector(current, selector)) return current;
      current = current.parentNode;
    }
    return null;
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
  const adminAgentFilter = createElement(document, "div", {
    id: "adminAgentFilter",
    className: "admin-agent-filter admin-only hidden",
  });
  const adminAgentHeader = createElement(document, "label", { className: "admin-agent-header" });
  const adminAgentToggle = createElement(document, "input", {
    id: "adminAgentToggleAll",
    attributes: { type: "checkbox" },
  });
  const adminAgentHeaderText = createElement(document, "span", { textContent: "Все заявки" });
  const adminAgentList = createElement(document, "div", { id: "adminAgentList", className: "admin-agent-list" });
  adminAgentHeader.appendChild(adminAgentToggle);
  adminAgentHeader.appendChild(adminAgentHeaderText);
  adminAgentFilter.appendChild(adminAgentHeader);
  adminAgentFilter.appendChild(adminAgentList);

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
  aside.appendChild(adminAgentFilter);
  aside.appendChild(sidebarBottom);
  document.body.appendChild(aside);
  document.body.appendChild(outside);
  return { document, aside, logoutForm, outside };
}

const DEFAULT_AGENTS = [
  { id: "a1", full_name: "Anna Agent" },
  { id: "b2", full_name: "Boris Trainee" },
  { id: "c3", full_name: "Cara Agent" },
];

function createFetchMock(role = "agent", profileOverrides = {}, options = {}) {
  const calls = [];
  const agents = options.agents || DEFAULT_AGENTS;
  const sessionResponses = [...(options.sessionResponses || [])];
  const agentResponses = [...(options.agentResponses || [])];
  const countResponses = [...(options.countResponses || [])];
  const sessionLoginTs = options.sessionLoginTs ?? 1;
  const byAssignee = options.byAssignee || {
    a1: { active: 5, frozen: 2 },
    b2: { active: 0, frozen: 7 },
    c3: { active: 4, frozen: 0 },
  };
  const fetchMock = async (path, options = {}) => {
    calls.push({ path: String(path), method: options.method || "GET", body: options.body || "" });
    let payload = {};
    if (path === "/api/v1/auth/session") {
      payload = sessionResponses.length ? sessionResponses.shift() : {
        real_user: { id: "u1", role, login: "ivan", full_name: "Ivan Petrov", availability_status: "waiting" },
        effective_user: { id: "u1", role, login: "ivan", full_name: "Ivan Petrov", availability_status: "waiting" },
        is_real_admin: role === "admin",
        session_login_ts: sessionLoginTs,
        app_timezone: "Asia/Tokyo",
      };
    } else if (path === "/api/v1/users/agents") {
      payload = agentResponses.length ? agentResponses.shift() : agents;
    } else if (path === "/api/v1/users/me/profile") {
      payload = {
        id: "u1",
        full_name: "Ivan Petrov",
        login: "ivan",
        avatar_present: false,
        avatar_url: null,
        ...profileOverrides,
      };
    } else if (String(path).startsWith("/api/v1/tickets/counts")) {
      payload = countResponses.length ? countResponses.shift() : { unassigned: 0, active: 1, special: 0, frozen: 0, closed: 0 };
      if (role === "admin") {
        payload.by_assignee ||= byAssignee;
      }
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

async function loadSidebar(document, role = "agent", profileOverrides = {}, options = {}) {
  const source = fs.readFileSync("api/app/static/sidebar.js", "utf8");
  const pathname = options.locationPathname || "/tickets";
  const href = options.locationHref || pathname;
  const window = new EventTargetLike();
  window.location = { pathname, href };
  window.__intervals = [];
  window.setInterval = (handler, delay) => {
    const id = window.__intervals.length + 1;
    window.__intervals.push({
      id,
      handler: async () => {
        handler();
        for (let index = 0; index < 10; index += 1) {
          await Promise.resolve();
        }
      },
      delay,
      cleared: false,
    });
    return id;
  };
  window.clearInterval = (id) => {
    const timer = window.__intervals.find((item) => item.id === id);
    if (timer) timer.cleared = true;
  };
  document.hidden = Boolean(options.hidden);
  class ImageMock {
    set src(value) {
      this._src = String(value || "");
      if (this._src.includes("missing")) {
        this.onerror?.();
      } else {
        this.onload?.();
      }
    }

    get src() {
      return this._src || "";
    }
  }
  const localStorage = new Map(Object.entries(options.storage || {}));
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
  const fetchMock = createFetchMock(role, profileOverrides, options);
  window.__fetchCalls = fetchMock.calls;
  window.__localStorage = localStorage;
  const context = vm.createContext({
    window,
    document,
    console: { info() {}, error() {}, log() {} },
    localStorage: storageApi,
    fetch: fetchMock,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
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
    Image: ImageMock,
  });
  vm.runInContext(source, context);
  await window.HelpdeskSidebar.init();
  return window;
}

async function flushSidebarAsync() {
  await Promise.resolve();
  await Promise.resolve();
}

function adminAgentItems(document) {
  return document.querySelectorAll(".admin-agent-item");
}

function clickAgent(document, index, ctrlKey = false) {
  const item = adminAgentItems(document)[index];
  item.dispatchEvent({ type: "click", target: item, ctrlKey });
  return item;
}

function storedAssignees(window) {
  return JSON.parse(window.__localStorage.get("hd_admin_assignee_ids") || "[]");
}

function sidebarAssignees(window) {
  return Array.from(window.HelpdeskSidebar.getState().adminAssigneeIds);
}

test("sidebar user menu init creates separate panel and binds interactions", async () => {
  const { document, logoutForm, outside } = buildSidebarDom();
  const window = await loadSidebar(document);

  assert.ok(window.HelpdeskSidebar);
  assert.equal((await window.HelpdeskSidebar.init()).appTimezone, "Asia/Tokyo");
  assert.equal(window.HelpdeskSidebar.getState().appTimezone, "Asia/Tokyo");
  const panel = document.querySelector("[data-sidebar-user-panel]");
  assert.ok(panel);
  assert.equal(panel.parentNode.classList.contains("sidebar-bottom"), true);

  const visibleName = document.querySelector("[data-sidebar-user-full-name]");
  assert.equal(visibleName.textContent, "Ivan Petrov");

  const legacyName = document.getElementById("sidebarUserDisplayName");
  assert.ok(legacyName);
  assert.ok(legacyName.hidden);
  assert.equal(panel.contains(legacyName), true);

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
    "Статистика",
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

test("admin agent filter supports click and ctrl click without empty selection", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1", "b2", "c3"]),
      hd_admin_assignee_login_ts: "1",
    },
  });

  assert.deepEqual(sidebarAssignees(window), ["a1", "b2", "c3"]);
  assert.equal(document.querySelector(".admin-agent-header span").textContent, "Все");
  assert.equal(document.querySelector(".admin-agent-header").classList.contains("pressed"), true);
  assert.deepEqual(adminAgentItems(document).map((item) => item.classList.contains("active")), [true, true, true]);

  clickAgent(document, 0);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1"]);
  assert.equal(document.querySelector(".admin-agent-header").classList.contains("pressed"), false);

  clickAgent(document, 1, true);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1", "b2"]);
  assert.deepEqual(adminAgentItems(document).map((item) => item.classList.contains("active")), [true, true, false]);

  clickAgent(document, 2, true);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1", "b2", "c3"]);
  assert.equal(document.querySelector(".admin-agent-header").classList.contains("pressed"), true);
  assert.deepEqual(adminAgentItems(document).map((item) => item.classList.contains("active")), [true, true, true]);

  clickAgent(document, 0, true);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1"]);
  assert.equal(document.querySelector(".admin-agent-header").classList.contains("pressed"), false);

  clickAgent(document, 1, true);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1", "b2"]);

  clickAgent(document, 0, true);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["b2"]);
  assert.notDeepEqual(sidebarAssignees(window), []);

  const header = document.querySelector(".admin-agent-header");
  header.dispatchEvent({ type: "click", target: header, ctrlKey: true });
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1", "b2", "c3"]);
  assert.equal(header.classList.contains("pressed"), true);
  assert.deepEqual(adminAgentItems(document).map((item) => item.classList.contains("active")), [true, true, true]);

  clickAgent(document, 2, true);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["c3"]);

  header.dispatchEvent({ type: "click", target: header });
  await flushSidebarAsync();
  assert.deepEqual(storedAssignees(window), ["a1", "b2", "c3"]);
});

test("admin agent filter click on tickets page keeps current location", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1", "b2", "c3"]),
      hd_admin_assignee_login_ts: "1",
      hd_tab: "frozen",
    },
    locationPathname: "/tickets",
    locationHref: "/tickets",
  });

  clickAgent(document, 1);
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["b2"]);
  assert.deepEqual(storedAssignees(window), ["b2"]);
  assert.equal(window.location.href, "/tickets");

  const header = document.querySelector(".admin-agent-header");
  header.dispatchEvent({ type: "click", target: header });
  await flushSidebarAsync();
  assert.deepEqual(sidebarAssignees(window), ["a1", "b2", "c3"]);
  assert.deepEqual(storedAssignees(window), ["a1", "b2", "c3"]);
  assert.equal(window.location.href, "/tickets");
});

test("admin agent filter click outside tickets navigates to stored tab", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1", "b2", "c3"]),
      hd_admin_assignee_login_ts: "1",
      hd_tab: "frozen",
    },
    locationPathname: "/management",
    locationHref: "/management",
  });

  clickAgent(document, 1);
  await flushSidebarAsync();

  assert.deepEqual(sidebarAssignees(window), ["b2"]);
  assert.deepEqual(storedAssignees(window), ["b2"]);
  assert.equal(window.location.href, "/tickets?tab=frozen");
});

test("admin agent filter outside tickets respects guard refusal for employee click", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1"]),
      hd_admin_assignee_login_ts: "1",
      hd_tab: "frozen",
    },
    locationPathname: "/management",
    locationHref: "/management",
  });
  let guardCalls = 0;
  window.HelpdeskAppShell = {
    confirmNavigateAway() {
      guardCalls += 1;
      return false;
    },
  };
  const callsBefore = window.__fetchCalls.length;

  clickAgent(document, 1);
  await flushSidebarAsync();

  assert.equal(guardCalls, 1);
  assert.deepEqual(sidebarAssignees(window), ["a1"]);
  assert.deepEqual(storedAssignees(window), ["a1"]);
  assert.equal(window.location.href, "/management");
  assert.equal(window.__fetchCalls.length, callsBefore);
  assert.deepEqual(adminAgentItems(document).map((item) => item.classList.contains("active")), [true, false, false]);
});

test("admin agent filter outside tickets respects guard refusal for all click", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["b2"]),
      hd_admin_assignee_login_ts: "1",
      hd_tab: "closed",
    },
    locationPathname: "/management",
    locationHref: "/management",
  });
  let guardCalls = 0;
  window.HelpdeskAppShell = {
    confirmNavigateAway() {
      guardCalls += 1;
      return false;
    },
  };
  const callsBefore = window.__fetchCalls.length;

  const header = document.querySelector(".admin-agent-header");
  header.dispatchEvent({ type: "click", target: header });
  await flushSidebarAsync();

  assert.equal(guardCalls, 1);
  assert.deepEqual(sidebarAssignees(window), ["b2"]);
  assert.deepEqual(storedAssignees(window), ["b2"]);
  assert.equal(window.location.href, "/management");
  assert.equal(window.__fetchCalls.length, callsBefore);
  assert.equal(header.classList.contains("pressed"), false);
});

test("admin agent filter all outside tickets selects all and navigates to closed tab", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["b2"]),
      hd_admin_assignee_login_ts: "1",
      hd_tab: "closed",
    },
    locationPathname: "/management",
    locationHref: "/management",
  });

  const header = document.querySelector(".admin-agent-header");
  header.dispatchEvent({ type: "click", target: header });
  await flushSidebarAsync();

  assert.deepEqual(sidebarAssignees(window), ["a1", "b2", "c3"]);
  assert.deepEqual(storedAssignees(window), ["a1", "b2", "c3"]);
  assert.equal(window.location.href, "/tickets?tab=closed");
});

test("admin agent filter outside tickets proceeds when guard allows", async () => {
  const { document } = buildSidebarDom();
  const navigations = [];
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1", "b2", "c3"]),
      hd_admin_assignee_login_ts: "1",
      hd_tab: "special",
    },
    locationPathname: "/management",
    locationHref: "/management",
  });
  window.HelpdeskAppShell = {
    confirmNavigateAway() {
      return true;
    },
    navigate(url, options) {
      navigations.push([url, options]);
      window.location.href = url;
      return true;
    },
  };

  clickAgent(document, 2);
  await flushSidebarAsync();

  assert.deepEqual(sidebarAssignees(window), ["c3"]);
  assert.deepEqual(storedAssignees(window), ["c3"]);
  assert.equal(navigations.length, 1);
  assert.equal(navigations[0][0], "/tickets?tab=special");
  assert.equal(navigations[0][1]?.skipGuard, true);
  assert.equal(window.location.href, "/tickets?tab=special");
});

test("admin agent filter keeps reload storage, resets on new login, and preserves first-run marker migration", async () => {
  let dom = buildSidebarDom();
  let window = await loadSidebar(dom.document, "admin", {}, {
    sessionLoginTs: 10,
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["b2"]),
      hd_admin_assignee_login_ts: "10",
    },
  });
  assert.deepEqual(sidebarAssignees(window), ["b2"]);
  assert.deepEqual(storedAssignees(window), ["b2"]);

  dom = buildSidebarDom();
  window = await loadSidebar(dom.document, "admin", {}, {
    sessionLoginTs: 11,
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["b2"]),
      hd_admin_assignee_login_ts: "10",
    },
  });
  assert.deepEqual(sidebarAssignees(window), ["a1"]);
  assert.deepEqual(storedAssignees(window), ["a1"]);

  dom = buildSidebarDom();
  window = await loadSidebar(dom.document, "admin", {}, {
    sessionLoginTs: 12,
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["c3"]),
    },
  });
  assert.deepEqual(sidebarAssignees(window), ["c3"]);
  assert.equal(window.__localStorage.get("hd_admin_assignee_login_ts"), "12");

  dom = buildSidebarDom();
  window = await loadSidebar(dom.document, "admin", {}, {
    sessionLoginTs: 13,
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["missing"]),
      hd_admin_assignee_login_ts: "13",
    },
  });
  assert.deepEqual(sidebarAssignees(window), ["a1"]);
});

test("admin agent counts render from loadCounts by_assignee without extra fetches and hide zeroes", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1"]),
      hd_admin_assignee_login_ts: "1",
    },
  });
  const callsAfterInit = window.__fetchCalls.length;
  const items = adminAgentItems(document);

  assert.equal(items[0].querySelector("[data-agent-count-active]").textContent, "5");
  assert.equal(items[0].querySelector("[data-agent-count-frozen]").textContent, "2");
  assert.equal(items[0].querySelector("[data-agent-availability-status]"), null);
  assert.equal(items[1].querySelector("[data-agent-count-active]").textContent, "");
  assert.equal(items[1].querySelector("[data-agent-count-frozen]").textContent, "7");
  assert.equal(items[2].querySelector("[data-agent-count-active]").textContent, "4");
  assert.equal(items[2].querySelector("[data-agent-count-frozen]").textContent, "");

  assert.equal(window.__fetchCalls.length, callsAfterInit);
  assert.equal(window.__fetchCalls.filter((call) => String(call.path).startsWith("/api/v1/tickets/counts")).length, 1);
  assert.equal(window.__fetchCalls.filter((call) => call.path === "/api/v1/users/agents").length, 1);
});

test("sidebar owns one shared poller and refreshes counts without rendering employee statuses in admin filter", async () => {
  const { document } = buildSidebarDom();
  const initialAgents = [
    { id: "a1", full_name: "Anna Agent", availability_status: "online" },
    { id: "b2", full_name: "Boris Trainee", availability_status: "waiting" },
  ];
  const refreshedAgents = [
    { id: "a1", full_name: "Anna Agent", availability_status: "on_ticket" },
    { id: "b2", full_name: "Boris Trainee", availability_status: "lunch" },
  ];
  const window = await loadSidebar(document, "admin", {}, {
    agents: initialAgents,
    agentResponses: [initialAgents, refreshedAgents],
    countResponses: [
      { unassigned: 1, active: 2, special: 3, frozen: 4, closed: 5, by_assignee: { a1: { active: 2, frozen: 0 }, b2: { active: 0, frozen: 4 } } },
      { unassigned: 6, active: 7, special: 8, frozen: 9, closed: 10, by_assignee: { a1: { active: 7, frozen: 1 }, b2: { active: 0, frozen: 9 } } },
    ],
    storage: {
      hd_admin_assignee_ids: JSON.stringify(["a1", "b2"]),
      hd_admin_assignee_login_ts: "1",
    },
  });

  assert.equal(window.__intervals.length, 1);
  assert.equal(window.__intervals[0].delay, 5000);
  assert.equal(window.HelpdeskSidebar.getState().counts.active, 2);
  assert.deepEqual(window.HelpdeskSidebar.getState().agents.map((agent) => agent.availability_status), ["online", "waiting"]);
  const firstItem = adminAgentItems(document)[0];

  await window.HelpdeskSidebar.refresh();

  assert.equal(window.__intervals.length, 1);
  assert.equal(window.HelpdeskSidebar.getState().counts.active, 7);
  assert.deepEqual(window.HelpdeskSidebar.getState().agents.map((agent) => agent.availability_status), ["on_ticket", "lunch"]);
  assert.equal(document.querySelector("[data-agent-count-active]").textContent, "7");
  assert.equal(document.querySelector("[data-agent-availability-status]"), null);
  assert.equal(adminAgentItems(document)[0], firstItem);
});

test("sidebar refresh moves active marker between existing employee status buttons", async () => {
  const { document } = buildSidebarDom({ statusPanel: "standard" });
  const baseSession = {
    real_user: { id: "u1", role: "agent", login: "ivan", full_name: "Ivan Petrov", availability_status: "online" },
    effective_user: { id: "u1", role: "agent", login: "ivan", full_name: "Ivan Petrov", availability_status: "online" },
    is_real_admin: false,
    session_login_ts: 1,
    app_timezone: "Asia/Tokyo",
  };
  const nextSession = {
    ...baseSession,
    real_user: { ...baseSession.real_user, availability_status: "on_ticket" },
    effective_user: { ...baseSession.effective_user, availability_status: "on_ticket" },
  };
  const window = await loadSidebar(document, "agent", {}, {
    sessionResponses: [baseSession, nextSession],
  });
  const onlineButton = document.querySelector("[data-availability-status='online']");
  const onTicketButton = document.querySelector("[data-availability-status='on_ticket']");

  assert.equal(onlineButton.classList.contains("active"), true);
  assert.equal(onTicketButton.classList.contains("active"), false);

  await window.HelpdeskSidebar.refresh();

  assert.equal(onlineButton.classList.contains("active"), false);
  assert.equal(onTicketButton.classList.contains("active"), true);
  assert.equal(document.querySelectorAll("[data-availability-status].active").length, 1);
});

test("sidebar repeated init and focus refresh do not create duplicate timers", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "admin", {}, {
    countResponses: [
      { unassigned: 0, active: 1, special: 0, frozen: 0, closed: 0, by_assignee: {} },
      { unassigned: 0, active: 11, special: 0, frozen: 0, closed: 0, by_assignee: {} },
    ],
  });

  await window.HelpdeskSidebar.init();
  await window.HelpdeskSidebar.init();
  assert.equal(window.__intervals.length, 1);

  await window.HelpdeskSidebar.refresh();

  assert.equal(window.__intervals.length, 1);
  assert.equal(window.HelpdeskSidebar.getState().counts.active, 11);
});

test("regular non-admin sidebar does not show employee filter", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "agent");

  assert.equal(document.querySelector("#adminAgentFilter").classList.contains("hidden"), true);
  assert.equal(adminAgentItems(document).length, 0);
  assert.equal(window.__fetchCalls.filter((call) => call.path === "/api/v1/users/agents").length, 1);
  assert.equal(window.__fetchCalls.filter((call) => String(call.path).startsWith("/api/v1/tickets/counts")).length, 1);
});

test("sidebar avatar uses profile avatar_url and keeps user menu clickable", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "agent", {
    avatar_present: true,
    avatar_url: "/api/v1/users/u1/avatar",
  });

  const avatarButton = document.querySelector("[data-sidebar-user-menu-toggle]");
  const menu = document.querySelector("[data-sidebar-user-menu]");

  assert.equal(window.__fetchCalls.some((call) => call.path === "/api/v1/users/me/profile"), true);
  assert.equal(avatarButton.dataset.sidebarAvatarImage, "1");
  assert.match(avatarButton.style.backgroundImage, /\/api\/v1\/users\/u1\/avatar/);
  avatarButton.dispatchEvent({ type: "click", target: avatarButton });
  assert.equal(menu.hidden, false);
});

test("sidebar avatar falls back to initials when avatar image fails to load", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document, "agent", {
    avatar_present: true,
    avatar_url: "/api/v1/users/u1/missing-avatar",
  });

  const avatarButton = document.querySelector("[data-sidebar-user-menu-toggle]");
  const menu = document.querySelector("[data-sidebar-user-menu]");

  assert.equal(avatarButton.textContent, "IP");
  assert.equal(avatarButton.dataset.sidebarAvatarImage, undefined);
  assert.equal(avatarButton.style.backgroundImage, "");
  assert.equal(window.HelpdeskSidebar.getState().avatarPresent, false);

  avatarButton.dispatchEvent({ type: "click", target: avatarButton });
  assert.equal(menu.hidden, false);
});

test("sidebar avatar falls back to initials and profile page can update avatar state", async () => {
  const { document } = buildSidebarDom();
  const window = await loadSidebar(document);
  const avatarButton = document.querySelector("[data-sidebar-user-menu-toggle]");

  assert.equal(avatarButton.textContent, "IP");
  assert.equal(avatarButton.dataset.sidebarAvatarImage, undefined);

  window.HelpdeskSidebar.setCurrentUserAvatar({
    avatar_present: true,
    avatar_url: "/api/v1/users/u1/avatar",
    avatar_display_url: "/api/v1/users/u1/avatar?v=123",
  });
  assert.equal(avatarButton.textContent, "");
  assert.equal(avatarButton.dataset.sidebarAvatarImage, "1");
  assert.match(avatarButton.style.backgroundImage, /v=123/);

  window.HelpdeskSidebar.setCurrentUserAvatar({ avatar_present: false, avatar_url: null });
  assert.equal(avatarButton.textContent, "IP");
  assert.equal(avatarButton.dataset.sidebarAvatarImage, undefined);
  assert.equal(avatarButton.style.backgroundImage, "");
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
    ["online", "on_ticket", "waiting", "lunch", "offline"],
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

for (const role of ["admin", "agent", "trainee"]) {
  test("notification bot menu entry is visible for role: " + role, async () => {
    const { document } = buildSidebarDom();
    const window = await loadSidebar(document, role);
    const item = document.querySelector('[data-sidebar-user-menu-item="bot-notifications"]');
    assert.equal(item.hidden, false);

    item.dispatchEvent({ type: "click", target: item });
    assert.equal(window.location.href, "/profile/notification-bot");
  });
}

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

for (const all of [false, true]) for (const approved of [false, true]) {
  test(`async assignee guard waits before mutations: all=${all}, approved=${approved}`, async () => {
    const { document } = buildSidebarDom();
    const window = await loadSidebar(document, 'admin', {}, { storage: {
      hd_admin_assignee_ids: JSON.stringify(['a1']), hd_admin_assignee_login_ts: '1', hd_tab: 'active',
    }, locationPathname: '/settings/bot-buttons', locationHref: '/settings/bot-buttons' });
    let answer;
    window.HelpdeskAppShell = { confirmNavigateAway() { return new Promise(resolve => { answer = resolve; }); } };
    const fetches = window.__fetchCalls.length;
    if (all) { const header = document.querySelector('.admin-agent-header'); header.dispatchEvent({ type: 'click', target: header }); }
    else clickAgent(document, 1);
    await flushSidebarAsync();
    assert.deepEqual(sidebarAssignees(window), ['a1']); assert.deepEqual(storedAssignees(window), ['a1']);
    assert.equal(window.location.href, '/settings/bot-buttons'); assert.equal(window.__fetchCalls.length, fetches);
    answer(approved); await flushSidebarAsync();
    assert.deepEqual(sidebarAssignees(window), approved ? all ? ['a1', 'b2', 'c3'] : ['b2'] : ['a1']);
    assert.equal(window.location.href, approved ? '/tickets?tab=active' : '/settings/bot-buttons');
  });
}
