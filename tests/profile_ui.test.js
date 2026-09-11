const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const template = fs.readFileSync(path.join(root, "api", "app", "templates", "profile.html"), "utf8");
const script = fs.readFileSync(path.join(root, "api", "app", "static", "profile.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "api", "app", "static", "styles.css"), "utf8");

test("profile page template contains expected read-only fields and edit modals", () => {
  assert.match(template, /data-shell-page="profile"/);
  assert.match(template, /id="profileAvatar"/);
  assert.match(template, /id="profileSuccessMessage"/);
  assert.match(template, /id="profileFullName"/);
  assert.match(template, /id="profileLogin"/);
  assert.match(template, /id="profilePasswordMask"[^>]*>••••••••</);
  assert.match(template, /id="profileRole"/);
  assert.match(template, /id="profileLegacyCount"/);
  assert.match(template, /id="profileTelegram"/);
  assert.doesNotMatch(template, /Активен/);
  assert.match(template, /id="loginModal"/);
  assert.match(template, /Введите новый логин/);
  assert.match(template, /id="passwordModal"/);
  assert.match(template, /Введите старый пароль/);
  assert.match(template, /id="currentPasswordInput" type="password"/);
  assert.match(template, /id="newPasswordInput" type="password"/);
  assert.match(template, /id="newPasswordConfirmationInput" type="password"/);
});

test("profile rows are separate data cards in a constrained left column", () => {
  assert.match(styles, /\.profile-details\s*\{[\s\S]*gap:\s*10px;[\s\S]*width:\s*min\(100%,\s*760px\);[\s\S]*max-width:\s*760px;[\s\S]*justify-self:\s*start;/);
  assert.match(
    styles,
    /\.profile-row\s*\{[\s\S]*border:\s*1px solid[\s\S]*border-radius:\s*12px;[\s\S]*padding:\s*14px 16px;[\s\S]*box-shadow:/,
  );
  assert.doesNotMatch(styles, /\.profile-row\s*\{[\s\S]*border-bottom:\s*1px solid var\(--border\);/);
});

test("editable profile rows keep edit buttons outside the data card", () => {
  assert.match(template, /class="profile-editable-row"[\s\S]*class="profile-row profile-row-editable"[\s\S]*id="profileLogin"[\s\S]*<\/div>\s*<button id="editLoginBtn"/);
  assert.match(template, /class="profile-editable-row"[\s\S]*class="profile-row profile-row-editable"[\s\S]*id="profilePasswordMask"[\s\S]*<\/div>\s*<button id="editPasswordBtn"/);
  assert.match(styles, /\.profile-editable-row\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto;[\s\S]*gap:\s*10px;/);
  assert.doesNotMatch(styles, /grid-template-columns:\s*minmax\(180px,\s*280px\) minmax\(0,\s*1fr\) 36px/);
  assert.doesNotMatch(template, /profile-value-wrap profile-value-action/);
});

test("profile typography is visually stronger", () => {
  assert.match(styles, /\.profile-hero-text h1\s*\{[\s\S]*font-size:\s*42px;/);
  assert.match(styles, /\.profile-label\s*\{[\s\S]*font-size:\s*18px;/);
  assert.match(styles, /\.profile-value\s*\{[\s\S]*font-size:\s*22px;/);
  assert.doesNotMatch(styles, /\.profile-label\s*\{[\s\S]*font-size:\s*19px;/);
  assert.doesNotMatch(styles, /\.profile-value\s*\{[\s\S]*font-size:\s*23px;/);
  assert.match(styles, /\.profile-edit-btn\s*\{[\s\S]*width:\s*48px;[\s\S]*height:\s*48px;[\s\S]*font-size:\s*28px;/);
});

test("profile script loads profile and renders safe fields", () => {
  assert.match(script, /api\("\/api\/v1\/users\/me\/profile"\)/);
  assert.match(script, /profile\.telegram_notification/);
  assert.match(script, /Привязан —/);
  assert.match(script, /Не привязан/);
  assert.match(script, /roleLabels/);
  assert.match(script, /function initials/);
  assert.doesNotMatch(script, /avatar_path[^|]*fetch/);
});

test("profile login and password modals call only self endpoints without user_id or admin API", () => {
  assert.match(script, /api\("\/api\/v1\/users\/me\/login"/);
  assert.match(script, /JSON\.stringify\(\{ login \}\)/);
  assert.match(script, /api\("\/api\/v1\/users\/me\/password"/);
  assert.match(script, /current_password: currentPassword/);
  assert.match(script, /new_password: newPassword/);
  assert.match(script, /new_password_confirmation: confirmation/);
  assert.doesNotMatch(script, /user_id/);
  assert.doesNotMatch(script, /\/api\/v1\/settings\/users/);
});

test("profile script maps modal errors and runs delayed logout flow", () => {
  assert.match(script, /login_already_used/);
  assert.match(script, /Этот логин уже используется/);
  assert.match(script, /current_password_invalid/);
  assert.match(script, /Старый пароль указан неверно/);
  assert.match(script, /password_confirmation_mismatch/);
  assert.match(script, /Новый пароль и подтверждение не совпадают/);
  assert.match(script, /successMessage:\s*document\.getElementById\("profileSuccessMessage"\)/);
  assert.match(script, /function showSuccessMessage\(message\)/);
  assert.match(script, /showSuccessMessage\(successText\);[\s\S]*setStatus\(successText\);[\s\S]*window\.setTimeout/);
  assert.match(script, /const RELOGIN_DELAY_MS = 2000;/);
  assert.match(script, /fetch\("\/logout"/);
  assert.match(script, /method: "POST"/);
  assert.match(script, /window\.location\.href = "\/login"/);
});

test("profile styles are scoped to profile shell page", () => {
  assert.match(styles, /\.main\[data-shell-page="profile"\]/);
  assert.match(styles, /\.profile-content/);
  assert.match(styles, /\.profile-avatar/);
  assert.match(styles, /\.profile-row/);
  assert.match(styles, /\.profile-success-message/);
  assert.match(styles, /\.profile-modal-dialog \.modal-body input/);
});
