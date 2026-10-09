const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const template = fs.readFileSync(path.join(root, "api", "app", "templates", "statuses.html"), "utf8");
const script = fs.readFileSync(path.join(root, "api", "app", "static", "statuses.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "api", "app", "static", "styles.css"), "utf8");

function sourceBetween(source, start, end) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("statuses page keeps existing shell and status board mount", () => {
  assert.match(template, /data-shell-page="statuses"/);
  assert.match(template, /id="statusBoardGrid"/);
  assert.match(template, /\/static\/statuses\.js\?v=20261009a/);
});

test("status board uses the shared visual status order", () => {
  assert.match(script, /const statusOrder = \["online", "on_ticket", "waiting", "lunch", "offline"\];/);
});

test("status cards render avatar before employee name and keep duration inside active status", () => {
  assert.match(script, /function initials\(fullName\)/);
  assert.match(script, /class="status-user-head"[\s\S]*class="status-user-avatar"[\s\S]*class="status-user-name"/);
  assert.match(script, /user\.avatar_present && user\.avatar_url/);
  assert.match(script, /<img class="status-user-avatar-image hidden" src="\$\{escapeHtml\(avatarUrl\)\}" alt="" \/>/);
  assert.match(script, /status-board-btn-time/);
  assert.match(script, /formatStatusDuration\(user\.updated_at\)/);
});

test("status avatar image shows on load and falls back to initials on error", () => {
  assert.match(script, /querySelectorAll\("\.status-user-avatar-image"\)/);
  assert.match(script, /image\.addEventListener\("load", \(\) => \{[\s\S]*image\.classList\.remove\("hidden"\);[\s\S]*fallback\?\.classList\.add\("hidden"\);/);
  assert.match(script, /image\.addEventListener\("error", \(\) => \{[\s\S]*image\.removeAttribute\("src"\);[\s\S]*image\.classList\.add\("hidden"\);[\s\S]*fallback\?\.classList\.remove\("hidden"\);/);
});

test("status card sizing is scoped to internal status-board elements", () => {
  const statusScope = sourceBetween(styles, "/* ===== statuses ===== */", ".settings-section-tabs {");
  assert.match(statusScope, /\.status-user-column\s*\{[\s\S]*flex:\s*0 0 320px;[\s\S]*padding:\s*18px;[\s\S]*gap:\s*14px;/);
  assert.match(statusScope, /\.status-user-avatar\s*\{[\s\S]*width:\s*64px;[\s\S]*height:\s*64px;[\s\S]*font-size:\s*23px;/);
  assert.match(statusScope, /\.status-user-name\s*\{[\s\S]*font-size:\s*25px;/);
  assert.match(statusScope, /\.status-board-btn\s*\{[\s\S]*padding:\s*14px 16px;[\s\S]*font-size:\s*21px;/);
  assert.match(statusScope, /\.status-board-btn-time\s*\{[\s\S]*margin-left:\s*auto;[\s\S]*font-size:\s*18px;/);
  assert.match(statusScope, /@media \(max-width:\s*760px\)/);
  assert.doesNotMatch(statusScope, /\.sidebar\s*\{/);
  assert.doesNotMatch(statusScope, /\.toolbar\s*\{/);
});
