const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..");
const template = fs.readFileSync(path.join(root, "api", "app", "templates", "profile.html"), "utf8");
const script = fs.readFileSync(path.join(root, "api", "app", "static", "profile.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "api", "app", "static", "styles.css"), "utf8");

function sourceBetween(source, start, end) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("profile page template contains expected read-only fields and edit modals", () => {
  assert.match(template, /data-shell-page="profile"/);
  assert.match(template, /id="profileAvatar"/);
  assert.match(template, /id="profileAvatarImage"/);
  assert.match(template, /id="profileAvatarInitials"/);
  assert.match(template, /id="avatarFileInput"[^>]*type="file"[^>]*accept="image\/jpeg,image\/png,image\/webp"/);
  assert.match(template, /id="changeAvatarBtn"[^>]*>Изменить аватар</);
  assert.match(template, /id="deleteAvatarBtn"[^>]*>Удалить аватар</);
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
  assert.match(template, /id="avatarCropModal"/);
  assert.match(template, /id="avatarCropCanvas"[^>]*width="512"[^>]*height="512"/);
  assert.match(template, /id="avatarZoomInput"[^>]*type="range"/);
  assert.match(template, /id="deleteAvatarModal"/);
  assert.match(template, /Удалить аватар\?/);
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
  assert.match(styles, /\.profile-editable-row\s*\{[\s\S]*position:\s*relative;[\s\S]*width:\s*100%;/);
  assert.match(styles, /\.profile-edit-btn\s*\{[\s\S]*position:\s*absolute;[\s\S]*left:\s*calc\(100% \+ 10px\);/);
  assert.doesNotMatch(styles, /grid-template-columns:\s*minmax\(180px,\s*280px\) minmax\(0,\s*1fr\) 36px/);
  assert.doesNotMatch(template, /profile-value-wrap profile-value-action/);
});

test("profile typography is visually stronger", () => {
  assert.match(styles, /\.profile-hero-text h1\s*\{[\s\S]*font-size:\s*42px;/);
  assert.match(styles, /\.profile-avatar\s*\{[\s\S]*width:\s*164px;[\s\S]*height:\s*164px;[\s\S]*font-size:\s*52px;/);
  assert.match(styles, /\.profile-avatar-image\s*\{[\s\S]*position:\s*absolute;[\s\S]*inset:\s*0;[\s\S]*width:\s*100%;[\s\S]*height:\s*100%;/);
  assert.match(styles, /\.profile-label\s*\{[\s\S]*font-size:\s*18px;/);
  assert.match(styles, /\.profile-value\s*\{[\s\S]*font-size:\s*22px;/);
  const labelStyle = sourceBetween(styles, ".profile-label {", ".profile-value {");
  assert.doesNotMatch(labelStyle, /font-size:\s*19px;/);
  const valueStyle = sourceBetween(styles, ".profile-value {", ".profile-edit-btn {");
  assert.doesNotMatch(valueStyle, /font-size:\s*23px;/);
  assert.match(styles, /\.profile-edit-btn\s*\{[\s\S]*width:\s*48px;[\s\S]*height:\s*48px;[\s\S]*font-size:\s*28px;/);
});

test("profile script loads profile and renders safe fields", () => {
  assert.match(script, /api\("\/api\/v1\/users\/me\/profile"\)/);
  assert.match(script, /profile\.telegram_notification/);
  assert.match(script, /profileData\?\.avatar_present && profileData\?\.avatar_url/);
  assert.match(script, /el\.avatarImage\.classList\.add\("hidden"\);[\s\S]*el\.avatarInitials\?\.classList\.remove\("hidden"\);[\s\S]*el\.avatarImage\.src = url;/);
  assert.match(script, /function handleAvatarImageLoad\(\)\s*\{[\s\S]*avatarImage\?\.classList\.remove\("hidden"\);[\s\S]*avatarInitials\?\.classList\.add\("hidden"\);/);
  assert.match(script, /function handleAvatarImageError\(\)/);
  assert.match(script, /function handleAvatarImageError\(\)\s*\{[\s\S]*avatarImage\.classList\.add\("hidden"\);[\s\S]*avatarInitials\?\.classList\.remove\("hidden"\);/);
  assert.match(script, /avatarImage\?\.addEventListener\("load", handleAvatarImageLoad\)/);
  assert.match(script, /avatarImage\?\.addEventListener\("error", handleAvatarImageError\)/);
  assert.match(script, /Привязан —/);
  assert.match(script, /Не привязан/);
  assert.match(script, /roleLabels/);
  assert.match(script, /function initials/);
  assert.doesNotMatch(script, /avatar_path[^|]*fetch/);
});

test("profile avatar upload validates file and sends cropped canvas blob only", () => {
  assert.match(script, /const AVATAR_MAX_BYTES = 5 \* 1024 \* 1024;/);
  assert.match(script, /\["image\/jpeg", \{ extension: "jpg", outputType: "image\/jpeg" \}\]/);
  assert.match(script, /\["image\/png", \{ extension: "png", outputType: "image\/png" \}\]/);
  assert.match(script, /\["image\/webp", \{ extension: "webp", outputType: "image\/webp" \}\]/);
  assert.match(script, /file\.size > AVATAR_MAX_BYTES/);
  assert.match(script, /Поддерживаются только JPG, PNG и WEBP/);
  assert.match(script, /Размер файла не должен превышать 5 МБ/);
  assert.match(script, /document\.createElement\("canvas"\)/);
  assert.match(script, /output\.width = AVATAR_OUTPUT_SIZE;[\s\S]*output\.height = AVATAR_OUTPUT_SIZE;/);
  assert.match(script, /function avatarExtensionForType\(type, fallback = "jpg"\)/);
  assert.match(script, /extension: avatarExtensionForType\(blob\.type \|\| preferredType/);
  assert.match(script, /formData\.append\("avatar", blob, `avatar\.\$\{extension\}`\)/);
  assert.match(script, /api\("\/api\/v1\/users\/me\/avatar", \{[\s\S]*method: "PUT",[\s\S]*body: formData/);
  assert.doesNotMatch(script, /formData\.append\("user_id"/);
  assert.doesNotMatch(script, /formData\.append\("avatar_path"/);
});

test("profile crop geometry has cover scale, drag clamp, zoom state and 512 output", () => {
  assert.match(script, /const AVATAR_OUTPUT_SIZE = 512;/);
  assert.match(script, /function minimumCoverScale\(image, cropSize\)/);
  assert.match(script, /Math\.max\(cropSize \/ width, cropSize \/ height\)/);
  assert.match(script, /function clampCropPosition\(\)/);
  assert.match(script, /Math\.min\(maxX, Math\.max\(-maxX/);
  assert.match(script, /function setZoom\(nextZoom\)/);
  assert.match(script, /cropState\.scale = cropState\.minScale \* zoom/);
  assert.match(script, /addEventListener\("pointerdown", startCropDrag\)/);
  assert.match(script, /addEventListener\("pointermove", moveCropDrag\)/);
  assert.match(script, /addEventListener\("pointerup", endCropDrag\)/);
});

test("profile avatar replace/delete updates UI and sidebar without logout", () => {
  assert.match(script, /function avatarCacheBustedUrl\(url\)/);
  assert.match(script, /avatarCacheBustedUrl\(result\?\.avatar_url\)/);
  assert.match(script, /HelpdeskSidebar\?\.setCurrentUserAvatar\?\./);
  assert.match(script, /api\("\/api\/v1\/users\/me\/avatar", \{ method: "DELETE" \}\)/);
  assert.match(script, /profile = \{ \.\.\.\(profile \|\| \{\}\), avatar_present: Boolean\(result\?\.avatar_present\), avatar_url: result\?\.avatar_url \|\| null \}/);
  assert.doesNotMatch(sourceBetweenFunction(script, "async function saveAvatarCrop()", "async function deleteAvatar()"), /\/logout|\/login/);
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
  assert.match(styles, /\.profile-avatar-actions/);
  assert.match(styles, /\.profile-crop-frame/);
  assert.match(styles, /\.profile-crop-ring/);
  assert.match(styles, /\.profile-modal-dialog \.modal-body input/);
});

function sourceBetweenFunction(source, start, end) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing end: ${end}`);
  return source.slice(startIndex, endIndex);
}
