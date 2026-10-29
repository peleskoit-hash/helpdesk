export async function prepareContourPNG(blob) {
  const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
  if (blob.type !== 'image/png' || signature.join(',') !== '137,80,78,71,13,10,26,10') throw new Error('Нужен готовый прозрачный PNG.');
  const bitmap = await createImageBitmap(blob);
  let canvas;
  try {
    if (!bitmap.width || !bitmap.height || Math.max(bitmap.width, bitmap.height) > 2048) throw new Error('Размер контура не должен превышать 2048×2048 px.');
    canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('Не удалось проверить PNG: Canvas 2D недоступен.');
    ctx.drawImage(bitmap, 0, 0);
    for (let attempt = 0; attempt <= 8; attempt++) {
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let visible = false, transparent = false;
      for (let p = 3; p < pixels.length; p += 4) {
        if (pixels[p] > 0) visible = true;
        if (pixels[p] < 255) transparent = true;
        if (visible && transparent) break;
      }
      if (!visible || !transparent) throw new Error('Нужен видимый контур на прозрачном фоне. Измените настройки обработки.');
      if (blob.size <= 2 * 1024 * 1024) return { blob, width: canvas.width, height: canvas.height };
      if (attempt === 8 || Math.max(canvas.width, canvas.height) <= 32) break;
      // Always resample the original contour, retaining alpha and aspect ratio.
      const factor = .75 ** (attempt + 1);
      canvas.width = Math.max(1, Math.round(bitmap.width * factor));
      canvas.height = Math.max(1, Math.round(bitmap.height * factor));
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      blob = await canvas.convertToBlob({ type: 'image/png' });
    }
    throw new Error('Не удалось уменьшить PNG до 2 МБ. Снизьте детализацию или измените кадрирование.');
  } finally { bitmap.close(); if (canvas) canvas.width = canvas.height = 1; }
}
