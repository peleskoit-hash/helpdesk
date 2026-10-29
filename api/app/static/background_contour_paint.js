import { imageBounds, contourLayout, contourMarkup } from '/static/contour_geometry.js?v=20261029-contour-spacing-v1';
const API = "/api/v1/background-contours";
let items = new Map(), available = false, loading = null, loadedAt = 0;
const images = new Map(), tiles = new Map();
let hooks;
export const isContour = value => typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);

export async function loadContours(force = false) {
  if (loading) return loading;
  if (available && !force) return [...items.values()];
  loading = (async () => {
    try {
      const response = await fetch(API, { credentials: "include", cache: "no-store" });
      if (!response.ok) throw new Error("Не удалось загрузить контуры");
      const payload = await response.json();
      if (!Array.isArray(payload.items) || !payload.items.every(item => isContour(item.id))) throw new Error("Некорректный ответ библиотеки контуров.");
      items = new Map(payload.items.map(item => [item.id, item]));
      available = true;
      loadedAt = Date.now();
      hooks?.catalogue?.(payload.items);
      if (hooks && Object.values(hooks.current().background_patterns || {}).some(theme => isContour(theme.selected) && !items.has(theme.selected))) void hooks.refresh();
      return payload.items;
    } catch (error) {
      available = false;
      loadedAt = Date.now();
      throw error;
    } finally { loading = null; hooks?.render(); }
  })();
  return loading;
}

async function image(id) {
  const response = await fetch(`${API}/${id}/image`, { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error("Изображение контура недоступно");
  const blob = await response.blob();
  if (!['image/png', 'image/webp'].includes(blob.type) || blob.size > 2097152) throw new Error("Некорректное изображение контура");
  const bitmap = await createImageBitmap(blob);
  const width = bitmap.width, height = bitmap.height;
  if (!width || !height || Math.max(width, height) > 2048) { bitmap.close(); throw new Error("Некорректный размер контура"); }
  let bounds;
  try { bounds = imageBounds(bitmap); } finally { bitmap.close(); }
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  return { data, width, height, bounds };
}

export function contourImage(id) {
  if (!images.has(id)) {
    if (images.size >= 8) {
      const active = new Set(Object.values(hooks?.current().background_patterns || {}).map(theme => theme.selected));
      const oldest = [...images.keys()].find(key => !active.has(key));
      if (oldest) images.delete(oldest);
    }
    const entry = { asset: null, failed: false };
    images.set(id, entry);
    entry.promise = image(id).then(asset => { entry.asset = asset; return asset; }).catch(error => { entry.failed = true; throw error; })
      .finally(() => {
        const current = hooks?.current();
        if (current?.background_patterns?.[current.theme_preference]?.selected === id) hooks.render();
      });
  }
  return images.get(id).promise;
}

export function paintContour(state, dark, area) {
  if (!available) {
    if (!loadedAt && !loading) { loadedAt = Date.now(); void loadContours().catch(() => {}); }
    return null;
  }
  const item = items.get(state.type);
  if (!item || ![item.rotation, item.rings, item.distance, item.base_scale].every(Number.isFinite) ||
      !Number.isInteger(item.rings) || item.rings < 0 || item.rings > 2 || item.rotation < 0 || item.rotation > 360 ||
      item.distance < 0 || item.distance > 200 || item.base_scale < 50 || item.base_scale > 200) return null;
  if (!images.has(item.id)) void contourImage(item.id).catch(() => {});
  const asset = images.get(item.id).asset;
  if (!asset) return null;
  const key = JSON.stringify([item, dark, state.intensity, state.variable, state.seed, state.scale, area]);
  if (!tiles.has(key)) {
    const layout = contourLayout(asset, item, area, state.scale);
    const content = contourMarkup(asset, item, layout, 'contour', asset.data, 'filter="url(#ink)"');
    const mask = state.variable ? `<filter id="noise"><feTurbulence type="fractalNoise" baseFrequency=".004" numOctaves="2" seed="${state.seed ?? 1}" stitchTiles="stitch"/><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 2 0 0 0 -.35"/></filter><mask id="variation" maskUnits="userSpaceOnUse" x="${-area.width / 2}" y="${-area.height / 2}" width="${area.width}" height="${area.height}"><rect x="${-area.width / 2}" y="${-area.height / 2}" width="${area.width}" height="${area.height}" filter="url(#noise)"/></mask>` : "";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${area.width}" height="${area.height}" viewBox="${-area.width / 2} ${-area.height / 2} ${area.width} ${area.height}" overflow="hidden"><defs><filter id="ink"><feFlood flood-color="${dark ? 'white' : 'black'}"/><feComposite in2="SourceAlpha" operator="in"/></filter>${mask}</defs><g opacity="${state.intensity / 100}" ${state.variable ? 'mask="url(#variation)"' : ''}>${content}</g></svg>`;

    // Keep only recent slider states, with a single embedded bitmap shared by all 25 uses.
    if (tiles.size >= 8) tiles.delete(tiles.keys().next().value);
    tiles.set(key, { image: `url("data:image/svg+xml,${encodeURIComponent(svg)}")`, size: `${area.width}px ${area.height}px` });
  }
  const result = tiles.get(key);
  return { image: result.image, size: result.size };
}

export function configureContourPaint(callbacks) {
  if (hooks) return;
  hooks = callbacks;
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('esdesk-appearance') : null;
  const usesContours = () => Object.values(hooks.current().background_patterns || {}).some(theme => isContour(theme.selected));
  let refreshing = null;
  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      for (const [id, entry] of images) if (entry.failed) images.delete(id);
      if (usesContours()) { loadedAt = 0; try { await loadContours(true); } catch { /* Paint falls back to the grid. */ } }
      await hooks.refresh();
    })().finally(() => { refreshing = null; });
    return refreshing;
  }
  if (channel) channel.onmessage = event => {
    if (event.data === 'contours') { available = false; loadedAt = 0; void refresh(); }
    if (event.data === 'appearance') void hooks.refresh();
  };
  let resizeFrame;
  window.addEventListener('resize', () => {
    if (!usesContours()) return;
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => hooks.render());
  });
  window.addEventListener('focus', () => { if (usesContours()) void refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && usesContours()) void refresh(); });
  setInterval(() => { if (!document.hidden && usesContours()) void refresh(); }, 15000);
  hooks.publish = kind => channel?.postMessage(kind);
  hooks.render();
}

export function notifyAppearance() { hooks?.publish?.('appearance'); }
export function notifyContourDeletion(id) {
  items.delete(id);
  images.delete(id);
  tiles.clear();
  hooks?.render();
  hooks?.publish?.('contours');
  void hooks?.refresh();
}
