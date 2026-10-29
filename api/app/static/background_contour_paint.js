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
  bitmap.close();
  if (!width || !height || Math.max(width, height) > 2048) throw new Error("Некорректный размер контура");
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  return { data, width, height };
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

export function paintContour(state, dark) {
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
  const key = JSON.stringify([item, dark, state.intensity, state.variable, state.seed]);
  if (!tiles.has(key)) {
    const size = 120 * item.base_scale / 100, step = size * (1 + item.distance / 100);
    const side = item.rings * 2 + 1, tile = 360 + (side - 1) * step;
    const width = size * asset.width / Math.max(asset.width, asset.height);
    const height = size * asset.height / Math.max(asset.width, asset.height);
    const elements = [];
    for (let y = -item.rings; y <= item.rings; y++) for (let x = -item.rings; x <= item.rings; x++) {
      elements.push(`<use href="#contour" x="${-width / 2}" y="${-height / 2}" transform="translate(${tile / 2 + x * step} ${tile / 2 + y * step}) rotate(${item.rotation})"/>`);
    }
    const mask = state.variable ? `<filter id="noise"><feTurbulence type="fractalNoise" baseFrequency=".004" numOctaves="2" seed="${state.seed ?? 1}" stitchTiles="stitch"/><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 2 0 0 0 -.35"/></filter><mask id="variation"><rect width="100%" height="100%" filter="url(#noise)"/></mask>` : "";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="840" height="840" viewBox="0 0 ${tile} ${tile}"><defs><filter id="ink"><feFlood flood-color="${dark ? 'white' : 'black'}"/><feComposite in2="SourceAlpha" operator="in"/></filter><image id="contour" width="${width}" height="${height}" href="${asset.data}" filter="url(#ink)"/>${mask}</defs><g opacity="${state.intensity / 1000}" ${state.variable ? 'mask="url(#variation)"' : ''}>${elements.join('')}</g></svg>`;
    // Keep only recent slider states, with a single embedded bitmap shared by all 25 uses.
    if (tiles.size >= 8) tiles.delete(tiles.keys().next().value);
    tiles.set(key, { image: `url("data:image/svg+xml,${encodeURIComponent(svg)}")`, size: tile });
  }
  const result = tiles.get(key);
  return { image: result.image, size: result.size * state.scale / 100 };
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
