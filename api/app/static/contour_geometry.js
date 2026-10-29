// A fixed reference gap lets auto-fit stay inside the existing 50–200% API range,
// even at the maximum manual distance. Changing distance never silently refits.
const REFERENCE_STEP = 1.5;
export function alphaBounds(pixels, width, height) {
  let left = width, top = height, right = -1, bottom = -1;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) if (pixels[(y * width + x) * 4 + 3]) {
    left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
  }
  return right < 0 ? null : { x: left, y: top, width: right - left + 1, height: bottom - top + 1 };
}
export function imageBounds(image) {
  const width = image.naturalWidth || image.width, height = image.naturalHeight || image.height;
  const canvas = new OffscreenCanvas(width, height);
  try {
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(image, 0, 0);
    const bounds = alphaBounds(ctx.getImageData(0, 0, width, height).data, width, height);
    if (!bounds) throw new Error('В изображении нет видимых контуров.');
    return bounds;
  } finally { canvas.width = canvas.height = 1; }
}
export function contourLayout(asset, composition, area, personalScale = 100) {
  const bounds = asset.bounds || { x: 0, y: 0, width: asset.width, height: asset.height };
  const longest = Math.max(bounds.width, bounds.height), w = bounds.width / longest, h = bounds.height / longest;
  const radians = composition.rotation * Math.PI / 180, cos = Math.abs(Math.cos(radians)), sin = Math.abs(Math.sin(radians));
  const rotatedWidth = w * cos + h * sin, rotatedHeight = w * sin + h * cos;
  const referenceWidth = rotatedWidth * (1 + 2 * composition.rings * REFERENCE_STEP);
  const referenceHeight = rotatedHeight * (1 + 2 * composition.rings * REFERENCE_STEP);
  const unit = Math.min(area.width * .94 / referenceWidth, area.height * .94 / referenceHeight) * composition.base_scale / 100 * personalScale / 100;
  const stepX = rotatedWidth * unit * (1 + composition.distance / 100);
  const stepY = rotatedHeight * unit * (1 + composition.distance / 100);
  return { width: w * unit, height: h * unit, stepX, stepY, bounds,
    extentWidth: rotatedWidth * unit + 2 * composition.rings * stepX,
    extentHeight: rotatedHeight * unit + 2 * composition.rings * stepY };
}
export function fitContourScale(asset, composition, area) {
  const layout = contourLayout(asset, { ...composition, base_scale: 100 }, area);
  return Math.max(50, Math.min(200, Math.floor(100 * Math.min(area.width * .94 / layout.extentWidth, area.height * .94 / layout.extentHeight) + 1e-6)));
}
export function contourMarkup(asset, composition, layout, id, href, filter = '') {
  const b = layout.bounds, elements = [];
  for (let y = -composition.rings; y <= composition.rings; y++) for (let x = -composition.rings; x <= composition.rings; x++) {
    elements.push(`<use href="#${id}" x="${-layout.width / 2}" y="${-layout.height / 2}" transform="translate(${x * layout.stepX} ${y * layout.stepY}) rotate(${composition.rotation})"/>`);
  }
  return `<defs><svg id="${id}" width="${layout.width}" height="${layout.height}" viewBox="${b.x} ${b.y} ${b.width} ${b.height}"><image width="${asset.width}" height="${asset.height}" href="${href}" ${filter}/></svg></defs>${elements.join('')}`;
}
