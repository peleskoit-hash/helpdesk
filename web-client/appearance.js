// Color calculations reused from agent theme.js; no preferences API or persistence.
export function setupAppearance(root) {
const appearanceMenu = root.querySelector('#appearanceMenu');
const appearance = { theme_preference: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' };
function defaults() {
  for (const mode of ['light', 'dark']) Object.assign(appearance, {
    [`interface_hue_${mode}`]: mode === 'dark' ? 225 : 259,
    [`interface_intensity_${mode}`]: 50, [`accent_hue_${mode}`]: 259, [`accent_strength_${mode}`]: 50,
  });
}
defaults();
const normalizeTheme = theme => theme === 'dark' ? 'dark' : 'light';
appearanceMenu.querySelector('.appearance-controls').innerHTML = [['interface', 'Основной цвет интерфейса'], ['accent', 'Цвет акцентов']].map(([role, label]) => `
  <fieldset><legend>${label}</legend><label for="${role}-hue">Оттенок</label>
  <input id="${role}-hue" class="appearance-hue" type="range" min="0" max="360" data-appearance-field="${role}_hue">
  <label for="${role}-intensity">Интенсивность</label>
  <input id="${role}-intensity" type="range" min="0" max="100" data-appearance-field="${role === 'interface' ? 'interface_intensity' : 'accent_strength'}"></fieldset>`).join('');
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
  root.dataset.theme = appearance.theme_preference;
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
  for (const [key, value] of Object.entries(tokens)) root.style.setProperty(key, value);
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


appearanceMenu.querySelectorAll('[data-appearance-theme]').forEach(button => button.addEventListener('click', () => applyTheme(button.dataset.appearanceTheme)));
appearanceMenu.querySelectorAll('[data-appearance-field]').forEach(input => input.addEventListener('input', () => {
  appearance[`${input.dataset.appearanceField}_${appearance.theme_preference}`] = Number(input.value);
  applyTheme(appearance.theme_preference);
}));
root.querySelector('#appearance-reset').addEventListener('click', () => { defaults(); applyTheme(appearance.theme_preference); });
applyTheme(appearance.theme_preference);
}
