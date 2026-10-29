function blur(source, width, height, radius) {
  const temp = new Float32Array(source.length), result = new Float32Array(source.length);
  const size = radius * 2 + 1;
  for (let y = 0; y < height; y++) {
    const row = y * width;
    let sum = 0;
    for (let k = -radius; k <= radius; k++) sum += source[row + Math.max(0, Math.min(width - 1, k))];
    for (let x = 0; x < width; x++) {
      temp[row + x] = sum / size;
      sum += source[row + Math.min(width - 1, x + radius + 1)] - source[row + Math.max(0, x - radius)];
    }
  }
  for (let x = 0; x < width; x++) {
    let sum = 0;
    for (let k = -radius; k <= radius; k++) sum += temp[Math.max(0, Math.min(height - 1, k)) * width + x];
    for (let y = 0; y < height; y++) {
      result[y * width + x] = sum / size;
      sum += temp[Math.min(height - 1, y + radius + 1) * width + x] - temp[Math.max(0, y - radius) * width + x];
    }
  }
  return result;
}

function thicken(mask, width, height, thickness) {
  if (thickness === 1) return mask;
  const left = Math.floor((thickness - 1) / 2), right = thickness - 1 - left;
  const temp = new Uint8Array(mask.length), result = new Uint8Array(mask.length);
  for (let y = 0; y < height; y++) {
    let count = 0;
    for (let k = 0; k <= Math.min(right, width - 1); k++) count += mask[y * width + k];
    for (let x = 0; x < width; x++) {
      temp[y * width + x] = count > 0 ? 1 : 0;
      if (x + right + 1 < width) count += mask[y * width + x + right + 1];
      if (x - left >= 0) count -= mask[y * width + x - left];
    }
  }
  for (let x = 0; x < width; x++) {
    let count = 0;
    for (let k = 0; k <= Math.min(right, height - 1); k++) count += temp[k * width + x];
    for (let y = 0; y < height; y++) {
      result[y * width + x] = count > 0 ? 1 : 0;
      if (y + right + 1 < height) count += temp[(y + right + 1) * width + x];
      if (y - left >= 0) count -= temp[(y - left) * width + x];
    }
  }
  return result;
}

function removeBorderContours(mask, width, height) {
  const queue = new Int32Array(mask.length);
  let start = 0, end = 0;
  const add = index => { if (mask[index]) { mask[index] = 0; queue[end++] = index; } };
  for (let x = 0; x < width; x++) { add(x); add((height - 1) * width + x); }
  for (let y = 0; y < height; y++) { add(y * width); add(y * width + width - 1); }
  while (start < end) {
    const index = queue[start++], x = index % width, y = Math.floor(index / width);
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      if (x + dx >= 0 && x + dx < width && y + dy >= 0 && y + dy < height) add(index + dy * width + dx);
    }
  }
}

// Pure pixel processing, also exercised by Node tests. The PNG is always black RGBA;
// theme colouring belongs only to CSS in the editor.
export function extractContours(data, width, height, settings) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
      width > 2048 || height > 2048 || data.length !== width * height * 4) throw new Error('Некорректный размер изображения.');
  const { sensitivity, detail, thickness, invert, cleanup } = settings;
  if (![sensitivity, detail, thickness].every(Number.isFinite) || sensitivity < 0 || sensitivity > 100 ||
      detail < 0 || detail > 100 || !Number.isInteger(thickness) || thickness < 1 || thickness > 10 ||
      typeof invert !== 'boolean' || typeof cleanup !== 'boolean') throw new Error('Некорректные настройки обработки.');
  const gray = new Float32Array(width * height);
  for (let i = 0; i < gray.length; i++) {
    const p = i * 4, alpha = data[p + 3] / 255;
    gray[i] = (data[p] * .2126 + data[p + 1] * .7152 + data[p + 2] * .0722) * alpha + 255 * (1 - alpha);
  }
  const radius = 1 + Math.round((100 - detail) / 33);
  const smooth = blur(gray, width, height, radius);
  const local = blur(smooth, width, height, radius + 3);
  const threshold = 70 - sensitivity * .65 + (100 - detail) * .05;
  let mask = new Uint8Array(width * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const top = Math.max(0, y - 1) * width, middle = y * width, bottom = Math.min(height - 1, y + 1) * width;
    const left = Math.max(0, x - 1), right = Math.min(width - 1, x + 1), p = middle + x;
    const gx = smooth[top + right] + 2 * smooth[middle + right] + smooth[bottom + right] - smooth[top + left] - 2 * smooth[middle + left] - smooth[bottom + left];
    const gy = smooth[bottom + left] + 2 * smooth[bottom + x] + smooth[bottom + right] - smooth[top + left] - 2 * smooth[top + x] - smooth[top + right];
    // Inversion selects the light rather than dark side of an edge, retaining a
    // transparent background instead of turning the entire canvas opaque.
    if (Math.hypot(gx, gy) / 4 >= threshold && (invert ? smooth[p] >= local[p] : smooth[p] <= local[p])) mask[p] = 1;
  }
  mask = thicken(mask, width, height, thickness);
  if (cleanup) removeBorderContours(mask, width, height);
  const pixels = new Uint8ClampedArray(data.length);
  let count = 0;
  for (let i = 0; i < mask.length; i++) if (mask[i]) { pixels[i * 4 + 3] = 255; count++; }
  return { pixels, count };
}

export function cropSize(width, height, crop) {
  if (![width, height, crop.x, crop.y, crop.width, crop.height].every(Number.isFinite) || width < 1 || height < 1 ||
      crop.x < 0 || crop.y < 0 || crop.width <= 0 || crop.height <= 0 || crop.x + crop.width > 100.001 || crop.y + crop.height > 100.001) throw new Error('Некорректная область кадрирования.');
  const sx = Math.min(width - 1, Math.floor(width * crop.x / 100)), sy = Math.min(height - 1, Math.floor(height * crop.y / 100));
  const sw = Math.max(1, Math.min(width - sx, Math.round(width * crop.width / 100)));
  const sh = Math.max(1, Math.min(height - sy, Math.round(height * crop.height / 100)));
  const ratio = Math.min(1, 2048 / Math.max(sw, sh));
  return { sx, sy, sw, sh, width: Math.max(1, Math.round(sw * ratio)), height: Math.max(1, Math.round(sh * ratio)) };
}

if (typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope) {
  const supported = typeof OffscreenCanvas === 'function' && typeof createImageBitmap === 'function' && typeof ImageData === 'function' && typeof OffscreenCanvas.prototype.convertToBlob === 'function';
  self.postMessage({ type: 'ready', supported });
  self.onmessage = async event => {
    const { id, blob, crop, settings } = event.data;
    let bitmap;
    try {
      if (!supported) throw new Error('Браузер не поддерживает локальную обработку фотографий.');
      bitmap = await createImageBitmap(blob);
      const area = cropSize(bitmap.width, bitmap.height, crop);
      const canvas = new OffscreenCanvas(area.width, area.height), ctx = canvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) throw new Error('Canvas 2D недоступен.');
      ctx.drawImage(bitmap, area.sx, area.sy, area.sw, area.sh, 0, 0, area.width, area.height);
      bitmap.close(); bitmap = null;
      const result = extractContours(ctx.getImageData(0, 0, area.width, area.height).data, area.width, area.height, settings);
      ctx.putImageData(new ImageData(result.pixels, area.width, area.height), 0, 0);
      const output = await canvas.convertToBlob({ type: 'image/png' });
      canvas.width = canvas.height = 1;
      self.postMessage({ id, blob: output, width: area.width, height: area.height, count: result.count });
    } catch (error) { self.postMessage({ id, error: error.message || 'Не удалось обработать фотографию.' }); }
    finally { bitmap?.close(); }
  };
}
