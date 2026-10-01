const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const listSource = fs.readFileSync("api/app/static/list.js", "utf8");
const styles = fs.readFileSync("api/app/static/styles.css", "utf8");
const template = fs.readFileSync("api/app/templates/tickets.html", "utf8");

function sourceBetween(source, start, end) {
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `start marker not found: ${start}`);
  const to = source.indexOf(end, from);
  assert.notEqual(to, -1, `end marker not found: ${end}`);
  return source.slice(from, to);
}

function loadColorClass() {
  const helperSource = [
    sourceBetween(listSource, "function ticketRowColorClass", "function tabKeyFromNode"),
    "globalThis.ticketRowColorClass = ticketRowColorClass;",
  ].join("\n");
  const context = vm.createContext({});
  vm.runInContext(helperSource, context);
  return context.ticketRowColorClass;
}

test("ticket list row color classification follows entity type and status priority", () => {
  const colorClass = loadColorClass();

  assert.equal(colorClass({ workflow_type: "ticket", status: "new" }, "active"), "ticket-color-request");
  assert.equal(colorClass({ workflow_type: "ticket", status: "unassigned" }, "unassigned"), "ticket-color-request");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "normal", status: "new" }, "active"), "ticket-color-task");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "group_parent", is_internal_task: true, status: "new" }, "active"), "ticket-color-group-task");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "group_child", status: "new" }, "active"), "ticket-color-group-task");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "normal", is_internal_task: true, status: "new" }, "active"), "ticket-color-internal-task");
  assert.equal(colorClass({ workflow_type: "ticket", is_frozen: true, status: "new" }, "active"), "ticket-color-frozen");
  assert.equal(colorClass({ workflow_type: "task", status: "closed", is_frozen: true }, "active"), "ticket-color-closed");
  assert.equal(colorClass({ workflow_type: "task", task_kind: "group_child", is_special: true, status: "new" }, "special"), "ticket-color-group-task");
});

test("ticket list render uses api seen flag as a visual modifier only", () => {
  const rowClassSource = sourceBetween(listSource, 'const rowClasses = ["row-click", "ticket-row"];', "tr.className = rowClasses.join");

  assert.match(rowClassSource, /rowClasses\.push\(ticketRowColorClass\(t, state\.tab\)\)/);
  assert.match(rowClassSource, /t\.seen_by_agent/);
  assert.match(rowClassSource, /rowClasses\.push\("ticket-row-seen"\)/);
  assert.match(rowClassSource, /state\.role === "agent" \|\| state\.role === "trainee"/);
  assert.match(rowClassSource, /t\.seen_by_agent === false/);
  assert.match(rowClassSource, /rowClasses\.push\("ticket-row-unseen"\)/);
  assert.doesNotMatch(rowClassSource, /state\.tab === "unassigned"[\s\S]*ticket-row-unassigned/);
  assert.doesNotMatch(rowClassSource, /state\.tab === "active"[\s\S]*ticket-row-fresh/);
});

test("ticket row color css applies gradients only to subject cells", () => {
  const ticketScope = sourceBetween(styles, "tr.ticket-row td.ticket-subject-col", "/* ===== ticket card/messages ===== */");

  for (const colorName of ["request", "task", "group-task", "internal-task", "frozen", "closed"]) {
    assert.match(ticketScope, new RegExp(`tr\\.ticket-color-${colorName} td\\.ticket-subject-col \\{[\\s\\S]*linear-gradient\\(90deg`));
  }
  assert.match(ticketScope, /tr\.ticket-row-seen\.ticket-color-request td\.ticket-subject-col/);
  assert.match(ticketScope, /tr\.ticket-row-seen\.ticket-color-task td\.ticket-subject-col/);
  assert.doesNotMatch(ticketScope, /tr\.ticket-color-[^{]+ td \{/);
  assert.doesNotMatch(ticketScope, /td:first-child[\s\S]*linear-gradient/);
});

test("ticket color preference is loaded, toggled, and persisted with a partial patch", () => {
  assert.match(template, /id="colorEnabledInput" type="checkbox"/);
  assert.match(template, /id="colorToggleText"[^>]*>Цвет<\/span>/);
  assert.match(template, /list\.js\?v=20261001a/);
  assert.match(template, /styles\.css\?v=20261001a/);

  const stateSource = sourceBetween(listSource, "const state = {", "};\n\nconst endpointMap");
  assert.match(stateSource, /colorEnabled: true/);

  const loadSource = sourceBetween(listSource, "async function loadPreferences", "async function updateColorPreference");
  assert.match(loadSource, /api\("\/api\/v1\/users\/me\/preferences"\)/);
  assert.match(loadSource, /preferences\?\.color_enabled !== false/);

  const updateSource = sourceBetween(listSource, "async function updateColorPreference", "async function loadCompletionStats");
  assert.match(updateSource, /apiWrite\("\/api\/v1\/users\/me\/preferences", \{ color_enabled: state\.colorEnabled \}\)/);
  assert.doesNotMatch(updateSource, /send_by_enter/);
  assert.match(updateSource, /state\.colorEnabled = previous/);
  assert.match(updateSource, /showError\(`Ошибка настройки цвета:/);

  assert.match(listSource, /await Promise\.all\(\[loadMe\(\), loadPreferences\(\)\]\)/);
});

test("ticket colors disabled state removes semantic backgrounds but keeps unread bold", () => {
  assert.match(styles, /body\.ticket-colors-disabled tr\.ticket-color-request td\.ticket-subject-col/);
  assert.match(styles, /body\.ticket-colors-disabled tr\.ticket-color-closed td\.ticket-check-cell/);
  assert.match(styles, /body\[data-theme="dark"\]\.ticket-colors-disabled tr\.ticket-color-request td\.ticket-subject-col/);
  assert.match(styles, /background: #fff;/);
  assert.match(styles, /tr\.ticket-row-unseen td,\s*tr\.ticket-row-unseen \.ticket-row-link,\s*tr\.ticket-row-unseen \.row-assignee-select \{[\s\S]*font-weight: 700;/);
});

test("ticket color legend is available only while colors are enabled and has exact swatches", () => {
  const legendSource = sourceBetween(template, '<span id="colorLegend"', "</span>\n        </label>");
  const labels = [...legendSource.matchAll(/color-legend-swatch[^"]*">([^<]+)<\/span>/g)].map((match) => match[1]);
  assert.deepEqual(labels, ["Задача", "Заявка", "Увольнение", "Внутренняя", "Замороженная", "Закрытая"]);

  for (const name of ["task", "request", "group-task", "internal-task", "frozen", "closed"]) {
    assert.match(styles, new RegExp(`\\.color-legend-${name} \\{[\\s\\S]*background: rgba\\(`));
  }
  assert.match(styles, /\.color-toggle:not\(\.color-toggle-disabled\):has\(#colorToggleText:hover\) \.color-legend/);
  assert.match(styles, /\.color-toggle:not\(\.color-toggle-disabled\):has\(#colorToggleText:focus-visible\) \.color-legend/);
  assert.match(styles, /\.color-toggle:not\(\.color-toggle-disabled\) \.color-legend:hover/);
  assert.match(listSource, /el\.colorToggleLabel\.classList\.toggle\("color-toggle-disabled", !state\.colorEnabled\)/);
});
