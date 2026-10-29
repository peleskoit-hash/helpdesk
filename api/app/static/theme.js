const LEGACY_THEME_STORAGE_KEY = "hd_theme";
const THEME_PREFERENCES_URL = "/api/v1/users/me/preferences";
// Legacy keys are accepted only when restoring preferences from an older server/client.
const LEGACY_ACCENT_HUES = { purple: 259, blue: 215, red: 350, green: 153, orange: 29, teal: 184, mono: 0 };
const COLOR_ROLES = ["interface_hue", "interface_intensity", "accent_hue", "accent_strength"];
const APPEARANCE_DEFAULTS = { theme_preference: "light", appearance_revision: null, background_patterns: null };
for (const theme of ["light", "dark"]) for (const role of COLOR_ROLES) {
  APPEARANCE_DEFAULTS[`${role}_${theme}`] = role === "interface_hue" && theme === "dark" ? 225 : role.endsWith("hue") ? 259 : 50;
}
let appearance = { ...APPEARANCE_DEFAULTS };
let confirmedAppearance = { ...appearance };
let preferencesLoadPromise = null;
let pendingAppearance = {};
let savingAppearance = {};
let appearanceSequence = 0;
let pendingAppearanceSequence = 0;
let appearanceWriterId = null;
let appearanceSessionPromise = null;
let saveInFlight = false;
let appearanceButton = null;
let appearanceMenu = null;
let contourPaint = null;
let appearanceRefreshNeeded = false;
let appearanceRefreshPromise = null;

function mergeAppearance(base, patch) {
  const merged = { ...base, ...patch };
  if (patch.background_patterns) {
    merged.background_patterns = { ...base.background_patterns };
    for (const [theme, changes] of Object.entries(patch.background_patterns)) {
      const previous = base.background_patterns?.[theme];
      const next = { ...previous, ...changes };
      if (Object.hasOwn(changes, "selected")) delete next.fallback_grid;
      if (changes.patterns) {
        next.patterns = { ...previous?.patterns };
        for (const [kind, parameters] of Object.entries(changes.patterns)) {
          next.patterns[kind] = { ...previous?.patterns?.[kind], ...parameters };
        }
      }
      merged.background_patterns[theme] = next;
    }
  }
  return merged;
}

function renderBackgroundPattern() {
  const mode = appearance.theme_preference, theme = appearance.background_patterns?.[mode];
  const type = theme?.selected ?? "grid";
  const state = { scale: 100, intensity: 50, variable: false, ...(!theme?.fallback_grid ? theme?.patterns?.[type] : {}), type, changed: !!theme && !theme.fallback_grid };
  const custom = !["none", "grid", "dots", "diagonals", "waves", "relief"].includes(type);
  document.body.toggleAttribute("data-pattern-custom", state.changed);
  document.body.dataset.patternKind = custom ? "contour" : state.type;
  document.body.dataset.patternVariable = String(state.variable);
  if (custom) {
    const paint = contourPaint?.paintContour(state, mode === "dark", { width: window.innerWidth, height: window.innerHeight });
    document.body.toggleAttribute("data-pattern-custom", !!paint);
    if (paint) {
      document.body.style.setProperty("--desk-pattern-image", paint.image);
      document.body.style.setProperty("--desk-contour-size", paint.size);
    }
  } else if (state.changed) {
    let image = "none";
    if (state.type !== "none") {
      const ink = mode === "dark" ? "white" : "black";
      const shapes = {
        grid: '<path d="M0 .5H42M.5 0V42"/>',
        dots: '<circle cx="21" cy="21" r="1.5" stroke="none" fill="currentColor"/>',
        diagonals: '<path d="M-1 1L1 -1M0 42L42 0M41 43L43 41"/>',
        waves: '<path d="M0 21C7 7 14 7 21 21S35 35 42 21"/>',
        relief: '<path d="M-42 0C-42 21 0 21 0 42M-21 0C-21 21 21 21 21 42M0 0C0 21 42 21 42 42M21 0C21 21 63 21 63 42M42 0C42 21 84 21 84 42"/>',
      };
      // Only the server creates seeds. Use a fixed preview until its first acknowledgement.
      const seed = state.seed ?? 1;
      // A stitched, low-frequency mask changes only pattern visibility, never the surface.
      const mask = state.variable ? `<filter id="noise" x="0" y="0" width="840" height="840" filterUnits="userSpaceOnUse"><feTurbulence type="fractalNoise" baseFrequency=".004" numOctaves="2" seed="${seed}" stitchTiles="stitch"/><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 2 0 0 0 -.35"/></filter><mask id="visibility"><rect width="840" height="840" filter="url(#noise)"/></mask>` : "";
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="840" height="840" viewBox="0 0 840 840"><defs><pattern id="p" width="42" height="42" patternUnits="userSpaceOnUse"><g color="${ink}" stroke="currentColor" fill="none" stroke-width="1">${shapes[state.type]}</g></pattern>${mask}</defs><rect width="840" height="840" fill="url(#p)" opacity="${state.variable ? .1 : state.intensity * .0008}" ${state.variable ? 'mask="url(#visibility)"' : ""}/></svg>`;
      image = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    }
    document.body.style.setProperty("--desk-pattern-image", image);
    document.body.style.setProperty("--desk-pattern-size", `${(state.type === "grid" && !state.variable ? 42 : 840) * state.scale / 100}px`);
    document.body.style.setProperty("--desk-pattern-strength", `${state.intensity * .08}%`);
  }
  if (!appearanceMenu) return;
  const section = appearanceMenu.querySelector(".appearance-pattern");
  section.querySelectorAll('input[name="appearance-pattern"]').forEach(input => { input.checked = input.value === state.type; });
  section.querySelectorAll('.contour-card').forEach(card => {
    const selected = card.dataset.contourId === state.type;
    card.classList.toggle('selected', selected);
    card.querySelector('.contour-select')?.setAttribute('aria-pressed', String(selected));
  });
  section.querySelector(".pattern-parameters").hidden = state.type === "none";
  section.querySelector("#patternIntensity").value = state.intensity;
  section.querySelector("#patternIntensity").disabled = state.variable;
  section.querySelector("#patternScale").value = state.scale;
  section.querySelector("#patternVariable").checked = state.variable;
  section.querySelector(".pattern-code span").textContent = state.variable && state.type !== "none" && state.seed ? String(state.seed) : "—";
}

function normalizeTheme(theme) { return theme === "dark" ? "dark" : "light"; }

function normalizeAppearance(payload) {
  const result = { ...APPEARANCE_DEFAULTS };
  result.theme_preference = normalizeTheme(payload?.theme_preference);
  result.appearance_revision = payload?.appearance_revision ?? null;
  result.background_patterns = payload?.background_patterns ?? null;
  for (const theme of ["light", "dark"]) {
    if (Object.hasOwn(LEGACY_ACCENT_HUES, payload?.accent_preference)) {
      result[`accent_hue_${theme}`] = LEGACY_ACCENT_HUES[payload.accent_preference];
      result[`accent_strength_${theme}`] = payload.accent_preference === "mono" ? 0 :
        (payload[`accent_intensity_${theme}`] ?? 50);
    }
    for (const role of COLOR_ROLES) {
      const key = `${role}_${theme}`, value = payload?.[key];
      if (Number.isInteger(value) && value >= 0 && value <= (role.endsWith("hue") ? 360 : 100)) result[key] = value;
    }
  }
  return result;
}

function rgbFromHsl(h, s, l) {
  const a = s * Math.min(l, 1 - l);
  return [0, 8, 4].map(n => {
    const k = (n + h / 30) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  });
}
function luminance(rgb) {
  return rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
}
function contrast(a, b) {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}
function cssRgb(rgb) { return `rgb(${rgb.join(", ")})`; }

function hslFromRgb(rgb) {
  const [r, g, b] = rgb.map(v => v / 255), max = Math.max(r, g, b), min = Math.min(r, g, b);
  const d = max - min, l = (max + min) / 2;
  const h = !d ? 0 : max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, d ? d / (1 - Math.abs(2 * l - 1)) : 0, l];
}
function readableOnSurfaces(rgb, surfaces, minimum = 4.5) {
  const target = luminance(surfaces[0]) > .179 ? 0 : 255;
  for (let i = 0; i <= 100; i++) {
    const candidate = rgb.map(v => Math.round(v + (target - v) * i / 100));
    if (surfaces.every(bg => contrast(candidate, bg) >= minimum)) return candidate;
  }
  return [target, target, target];
}

function applyTheme(theme) {
  appearance.theme_preference = normalizeTheme(theme);
  document.body.dataset.theme = appearance.theme_preference;
  const dark = appearance.theme_preference === "dark", mode = appearance.theme_preference;
  const hue = appearance[`interface_hue_${mode}`], strength = appearance[`interface_intensity_${mode}`];
  // Preserve the existing surface lightness hierarchy and the original default palette.
  const base = dark
    ? { bg: [23,27,40], surface: [32,37,53], soft: [38,43,61], border: [54,59,80], text: [237,234,245], muted: [173,169,191], backdrop: [60,51,84] }
    : { bg: [243,241,247], surface: [255,254,253], soft: [246,244,249], border: [228,224,236], text: [48,44,64], muted: [116,110,132], backdrop: [236,229,246] };
  const palette = Object.fromEntries(Object.entries(base).map(([key, rgb]) => {
    const [h, s, l] = hslFromRgb(rgb);
    return [key, rgbFromHsl((h + hue - (dark ? 225 : 259) + 360) % 360, Math.min(.65, s * strength / 50), l)];
  }));
  const intensity = appearance[`accent_strength_${mode}`], accentHue = appearance[`accent_hue_${mode}`];
  let accent = rgbFromHsl(accentHue % 360, Math.min(1, intensity / 10) * (.12 + intensity * .007),
    dark ? .78 - intensity * .001 : .52 - intensity * .0014);
  if (accentHue === 259 && intensity === 50) accent = dark ? [182,160,226] : [119,96,168];
  const mix = amount => palette.surface.map((v, i) => Math.round(v + (accent[i] - v) * amount));
  let tint = mix(.08 + intensity * .003), hover = mix(.12), primary = accent;
  // The old purple at 50 remains the default; arbitrary hues use the same contrast rules.
  if (hue === (dark ? 225 : 259) && strength === 50 && accentHue === 259 && intensity === 50) {
    tint = dark ? [60,51,84] : [236,229,246];
    hover = dark ? [48,53,75] : [238,235,245];
    primary = dark ? [218,199,250] : [98,74,145];
  }
  if (!dark) {
    // Near-white HSL bases hid Hue. Give large light surfaces distinct tinted levels.
    // Apply after accent mixing so the existing accent palette stays unchanged.
    const amount = strength / 100;
    for (const [key, [lightness, depth]] of Object.entries({
      bg: [.96,.06], surface: [.99,.05], soft: [.975,.055], backdrop: [.94,.06],
    })) palette[key] = rgbFromHsl(hue % 360, .6 * amount, lightness - depth * amount);
  }
  // A bright yellow tint must still support light body text in dark mode.
  if (dark) tint = readableOnSurfaces(tint, [[255,255,255]]);
  const surfaces = [palette.surface, palette.soft, palette.bg, palette.backdrop, hover, tint];
  palette.border = readableOnSurfaces(palette.border, [palette.surface, palette.soft, palette.bg], 1.35);
  const textSurfaces = [...surfaces, palette.border, ...(dark
    ? [[37,60,56], [70,46,64], [64,56,46], [42,53,78], [73,57,46], [36,63,62]]
    : [[234,244,239], [250,234,240], [249,241,227], [234,240,250], [255,237,213], [204,251,241]])];
  palette.text = readableOnSurfaces(palette.text, textSurfaces);
  palette.muted = readableOnSurfaces(palette.muted, textSurfaces);
  const tokens = Object.fromEntries(Object.entries(palette).map(([key, rgb]) => [`--desk-${key}`, cssRgb(rgb)]));
  Object.assign(tokens, {
    "--desk-accent": cssRgb(accent), "--desk-tint": cssRgb(tint), "--desk-hover": cssRgb(hover),
    "--desk-primary": cssRgb(primary), "--desk-on-tint": cssRgb(readableOnSurfaces(accent, textSurfaces)),
    "--desk-focus": cssRgb(readableOnSurfaces(accent, textSurfaces)),
    "--desk-on-accent": contrast(primary, [255,255,255]) >= 4.5 ? "#ffffff" : "#000000",
  });
  tokens["--accent"] = tokens["--desk-focus"];
  for (const [key, value] of Object.entries(tokens)) document.body.style.setProperty(key, value);
  renderBackgroundPattern();
  if (!appearanceMenu) return;
  appearanceMenu.querySelectorAll("[data-appearance-theme]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.appearanceTheme === mode));
  });
  appearanceMenu.querySelectorAll("[data-appearance-field]").forEach(input => {
    input.value = appearance[`${input.dataset.appearanceField}_${mode}`];
    const role = input.dataset.appearanceField.startsWith("interface_") ? "interface" : "accent";
    input.style.setProperty("--appearance-hue", appearance[`${role}_hue_${mode}`]);
  });
}

function setAppearanceStatus(message) {
  const status = appearanceMenu?.querySelector(".appearance-status");
  if (status) status.textContent = message;
}

async function fetchAppearance() {
  const response = await fetch(THEME_PREFERENCES_URL, {
    headers: { accept: "application/json" }, credentials: "include", cache: "no-store",
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return normalizeAppearance(await response.json());
}

async function refreshAppearance() {
  if (saveInFlight) { appearanceRefreshNeeded = true; return; }
  if (appearanceRefreshPromise) return appearanceRefreshPromise;
  const sequence = appearanceSequence;
  appearanceRefreshPromise = (async () => {
    try {
      const preferences = await fetchAppearance();
      if (saveInFlight || sequence !== appearanceSequence) { appearanceRefreshNeeded = true; return; }
      confirmedAppearance = preferences;
      appearance = mergeAppearance(confirmedAppearance, pendingAppearance);
      applyTheme(appearance.theme_preference);
    } catch { /* Keep the last acknowledgement; contour painting has its own safe grid fallback. */ }
    finally {
      appearanceRefreshPromise = null;
      if (appearanceRefreshNeeded && !saveInFlight) { appearanceRefreshNeeded = false; void refreshAppearance(); }
    }
  })();
  return appearanceRefreshPromise;
}

async function loadThemePreference() {
  if (!preferencesLoadPromise) {
    preferencesLoadPromise = (async () => {
      try {
        confirmedAppearance = await fetchAppearance();
        appearance = mergeAppearance(confirmedAppearance, pendingAppearance);
        applyTheme(appearance.theme_preference);
      } catch {
        setAppearanceStatus("Не удалось загрузить оформление. Попробуйте обновить страницу.");
        preferencesLoadPromise = null;
      }
    })();
  }
  return preferencesLoadPromise;
}

function getAppearanceWriter(patch, sequence) {
  if (!appearanceSessionPromise) {
    appearanceSessionPromise = (async () => {
      const response = await fetch(`${THEME_PREFERENCES_URL}/appearance-session`, {
        method: "POST", credentials: "include", keepalive: true,
        headers: { accept: "application/json", "content-type": "application/json" },
        body: JSON.stringify({ preferences: patch, sequence, expected_revision: confirmedAppearance.appearance_revision }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (typeof payload.writer_id !== "string") throw new Error("Invalid appearance session");
      appearanceWriterId = payload.writer_id;
      if (payload.preferences) confirmedAppearance = normalizeAppearance(payload.preferences);
      return appearanceWriterId;
    })().catch(error => {
      appearanceSessionPromise = null;
      throw error;
    });
  }
  return appearanceSessionPromise;
}

function appearanceWriteBody(patch, sequence) {
  return JSON.stringify({ ...patch, appearance_write: { writer_id: appearanceWriterId, sequence } });
}

async function flushAppearanceSaveQueue() {
  if (saveInFlight) return;
  saveInFlight = true;
  try {
    await loadThemePreference();
    while (Object.keys(pendingAppearance).length) {
      const patch = pendingAppearance;
      const sequence = pendingAppearanceSequence;
      pendingAppearance = {};
      savingAppearance = patch;
      let reconciled = false;
      try {
        await getAppearanceWriter(patch, sequence);
        const response = await fetch(THEME_PREFERENCES_URL, {
          method: "PATCH", credentials: "include", keepalive: true,
          headers: { accept: "application/json", "content-type": "application/json" },
          body: appearanceWriteBody(patch, sequence),
        });
        if (!response.ok) {
          if (response.status === 409) {
            const payload = await response.json();
            if (payload?.detail?.error?.code === "appearance_session_expired") {
              confirmedAppearance = normalizeAppearance(payload.detail.preferences);
              reconciled = true;
              appearanceWriterId = null;
              appearanceSessionPromise = null;
            }
          }
          throw new Error(`HTTP ${response.status}`);
        }
        confirmedAppearance = normalizeAppearance(await response.json());
        contourPaint?.notifyAppearance();
        setAppearanceStatus("");
      } catch {
        if (!reconciled) {
          try { confirmedAppearance = await fetchAppearance(); } catch { /* Retain the last server acknowledgement offline. */ }
        }
        setAppearanceStatus("Не удалось подтвердить сохранение оформления. Проверьте настройки.");
      }
      appearance = mergeAppearance(confirmedAppearance, pendingAppearance);
      applyTheme(appearance.theme_preference);
      savingAppearance = {};
    }
  } finally {
    saveInFlight = false;
    if (appearanceRefreshNeeded) { appearanceRefreshNeeded = false; void refreshAppearance(); }
  }
}

function changeAppearance(patch) {
  pendingAppearanceSequence = ++appearanceSequence;
  appearance = mergeAppearance(appearance, patch);
  pendingAppearance = mergeAppearance(pendingAppearance, patch);
  applyTheme(appearance.theme_preference);
  void flushAppearanceSaveQueue();
}

function positionAppearanceMenu() {
  if (!appearanceMenu?.matches(":popover-open")) return;
  const library = appearanceMenu.querySelector('.pattern-library'), first = library?.querySelector('.contour-card');
  if (first && appearanceMenu.querySelector('.appearance-pattern').open) {
    const top = library.getBoundingClientRect().top, row = first.getBoundingClientRect();
    library.style.maxHeight = `${Math.ceil(row.top - top + library.scrollTop + row.height / 2 + 1)}px`;
  } else if (!first) library?.style.removeProperty('max-height');
  const rect = appearanceButton.getBoundingClientRect();
  appearanceMenu.style.top = `${rect.bottom + 8}px`;
  appearanceMenu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - appearanceMenu.offsetWidth - 8))}px`;
  appearanceMenu.style.maxHeight = `${Math.max(0, window.innerHeight - rect.bottom - 16)}px`;
}

function initThemeToggle() {
  try { localStorage.removeItem(LEGACY_THEME_STORAGE_KEY); } catch { /* Storage may be unavailable. */ }
  const button = document.getElementById("themeToggleBtn");
  if (button && button !== appearanceButton) {
    if (!document.getElementById("appearancePatternStyles")) {
      const styles = document.createElement("link");
      styles.id = "appearancePatternStyles";
      styles.rel = "stylesheet";
      styles.href = "/static/appearance-pattern.css?v=20261029-appearance-compact-v1";
      document.head.appendChild(styles);
    }
    appearanceMenu?.querySelector('.appearance-pattern')?.dispatchEvent(new Event('contour-destroy'));
    appearanceMenu?.remove();
    appearanceButton = button;
    button.textContent = "Оформление";
    button.setAttribute("aria-label", "Оформление");
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("popovertarget", "appearanceMenu");
    appearanceMenu = document.createElement("div");
    appearanceMenu.id = "appearanceMenu";
    appearanceMenu.className = "appearance-menu";
    appearanceMenu.setAttribute("popover", "auto");
    appearanceMenu.setAttribute("role", "dialog");
    appearanceMenu.setAttribute("aria-label", "Оформление");
    appearanceMenu.innerHTML = `
      <div class="appearance-themes" role="group" aria-label="Тема">
        <button type="button" data-appearance-theme="light">Светлая</button>
        <button type="button" data-appearance-theme="dark">Тёмная</button>
      </div>
      <div class="appearance-controls">
        ${[["interface", "Основной цвет интерфейса"], ["accent", "Цвет акцентов"]].map(([role, label]) => `
          <fieldset>
            <legend>${label}</legend>
            <label for="appearance-${role}-hue">Оттенок</label>
            <input id="appearance-${role}-hue" class="appearance-hue" data-appearance-field="${role}_hue" type="range" min="0" max="360" step="1">
            <label for="appearance-${role}-intensity">Интенсивность</label>
            <input id="appearance-${role}-intensity" data-appearance-field="${role === "interface" ? "interface_intensity" : "accent_strength"}" type="range" min="0" max="100" step="1">
          </fieldset>`).join("")}
      </div>
      <details class="appearance-pattern">
        <summary>Фоновый узор</summary>
        <div class="pattern-library">
        <fieldset class="pattern-cards">
          <legend class="pattern-sr-only">Вид узора</legend>
          ${[["none", "Без узора"], ["grid", "Сетка"], ["dots", "Точки"], ["diagonals", "Диагонали"], ["waves", "Волны"], ["relief", "Рельеф"]].map(([value, label]) => `
            <label class="pattern-card">
              <input type="radio" name="appearance-pattern" value="${value}" ${value === "grid" ? "checked" : ""}>
              <span class="pattern-sample pattern-${value}" aria-hidden="true"></span>
              <span>${label}</span>
            </label>`).join("")}
        </fieldset>
          <h3 class="contour-heading" id="contourLibraryHeading">Пользовательские</h3>
          <div class="contour-list" aria-labelledby="contourLibraryHeading"></div>
          <p class="contour-status" role="status" aria-live="polite"></p>
          <button type="button" class="action contour-retry" hidden>Повторить загрузку</button>
        </div>
        <div class="contour-create">
          <button type="button" class="action" data-contour-create aria-describedby="contourCreateHint">Добавить свой узор +</button>
          <small id="contourCreateHint">Создайте прозрачный контур и добавьте его в общую библиотеку.</small>
        </div>
        <div class="pattern-parameters">
          <label class="pattern-switch" for="patternVariable">
            <span>Разноинтенсивный узор</span><input id="patternVariable" type="checkbox" role="switch">
          </label>
          <label for="patternIntensity">Интенсивность</label>
          <input id="patternIntensity" type="range" min="0" max="100" value="50">
          <label for="patternScale">Масштаб</label>
          <input id="patternScale" type="range" min="50" max="200" value="100">
          <div class="pattern-code">Код рисунка <span>—</span></div>
        </div>
      </details>
      <div class="appearance-status" role="status" aria-live="polite"></div>`;
    const patternSection = appearanceMenu.querySelector(".appearance-pattern");
    const createContourButton = patternSection.querySelector('[data-contour-create]');
    createContourButton.addEventListener('click', async () => {
      try {
        const { openContourEditor } = await import('/static/contour_editor.js?v=20261029-appearance-compact-v1');
        if (createContourButton.isConnected) await openContourEditor(createContourButton);
      } catch {
        patternSection.querySelector('#contourCreateHint').textContent = 'Не удалось открыть редактор. Попробуйте ещё раз.';
      }
    });
    void import("/static/appearance_contours.js?v=20261029-appearance-compact-v1").then(module => {
      contourPaint = module;
      module.configureContourPaint({ current: () => appearance, refresh: refreshAppearance, render: renderBackgroundPattern,
        catalogue: items => appearanceMenu?.querySelector('.appearance-pattern')?.dispatchEvent(new CustomEvent('contour-catalogue', { detail: items })) });
      if (patternSection.isConnected) module.initContourLibrary(patternSection, () => { positionAppearanceMenu(); renderBackgroundPattern(); },
        id => changeAppearance({ background_patterns: { [appearance.theme_preference]: { selected: id } } }));
    }).catch(() => {
      patternSection.querySelector(".contour-status").textContent = "Не удалось загрузить библиотеку. Обновите страницу.";
    });
    patternSection.addEventListener("toggle", positionAppearanceMenu);
    patternSection.addEventListener("input", event => {
      const mode = appearance.theme_preference, input = event.target;
      const kind = appearance.background_patterns?.[mode]?.selected ?? "grid";
      let changes;
      if (input.name === "appearance-pattern") changes = { selected: input.value };
      else if (input.id === "patternVariable") changes = { patterns: { [kind]: { variable: input.checked } } };
      else if (input.id === "patternIntensity") changes = { patterns: { [kind]: { intensity: Number(input.value) } } };
      else if (input.id === "patternScale") changes = { patterns: { [kind]: { scale: Number(input.value) } } };
      else return;
      if (appearance.background_patterns?.[mode]?.fallback_grid && input.name !== "appearance-pattern") {
        changes.selected = "grid";
        changes.patterns.grid = { scale: 100, intensity: 50, variable: false, ...changes.patterns.grid };
      }
      changeAppearance({ background_patterns: { [mode]: changes } });
      positionAppearanceMenu();
    });
    // Follow the invoker in DOM order for native popover keyboard navigation.
    button.after(appearanceMenu);
    appearanceMenu.addEventListener("beforetoggle", event => {
      if (event.newState === "open") patternSection.open = false;
    });
    appearanceMenu.addEventListener("toggle", event => {
      button.setAttribute("aria-expanded", String(event.newState === "open"));
      positionAppearanceMenu();
    });
    appearanceMenu.addEventListener("click", event => {
      const theme = event.target.closest("[data-appearance-theme]")?.dataset.appearanceTheme;
      if (theme) changeAppearance({ theme_preference: theme });
    });
    appearanceMenu.addEventListener("input", event => {
      const field = event.target.dataset.appearanceField;
      if (COLOR_ROLES.includes(field)) changeAppearance({ [`${field}_${appearance.theme_preference}`]: Number(event.target.value) });
    });
  }
  applyTheme(appearance.theme_preference);
  void loadThemePreference();
  document.querySelectorAll("form[action='/logout']").forEach(form => {
    if (form.dataset.themeLogoutBound === "1") return;
    form.dataset.themeLogoutBound = "1";
    form.addEventListener("submit", () => {
      for (const key of ["hd_role", "hd_user_role", "hd_user_id", "hd_admin_assignee_ids", "hd_tab_counts", "hd_agents_cache", LEGACY_THEME_STORAGE_KEY]) {
        try { localStorage.removeItem(key); } catch { /* Storage may be unavailable. */ }
      }
    });
  });
}

window.addEventListener("resize", positionAppearanceMenu);
window.addEventListener("scroll", positionAppearanceMenu, true);
window.addEventListener("pagehide", () => {
  if (!appearanceWriterId || !Object.keys(pendingAppearance).length) return;
  // Preserve the final slider input if a full navigation happens during a save.
  void fetch(THEME_PREFERENCES_URL, {
    method: "PATCH", credentials: "include", keepalive: true,
    headers: { accept: "application/json", "content-type": "application/json" },
    body: appearanceWriteBody(mergeAppearance(savingAppearance, pendingAppearance), pendingAppearanceSequence),
  }).catch(() => {});
});
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initThemeToggle);
else initThemeToggle();
window.HelpdeskTheme = { applyTheme, initThemeToggle };
