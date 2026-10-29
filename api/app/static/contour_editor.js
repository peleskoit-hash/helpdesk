const MAX_BYTES = 20 * 1024 * 1024;
let active = null, opening = null, styles = null;

function ensureStyles() {
  if (!styles) styles = new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/contour-editor.css?v=20261029-contour-editor-v1';
    link.onload = resolve;
    link.onerror = () => { link.remove(); styles = null; reject(new Error('Не удалось загрузить редактор')); };
    document.head.appendChild(link);
  });
  return styles;
}

export function openContourEditor(invoker = document.activeElement) {
  if (active) { active.focus(); return Promise.resolve(active); }
  if (opening) return opening;
  opening = (async () => {
    await ensureStyles();
    if (!window.HelpdeskDialogs) await import('/static/desk_dialogs.js?v=20261027-dialog-prompts-v4');
    return createEditor(invoker);
  })().finally(() => { opening = null; });
  return opening;
}

function createEditor(invoker) {
  const menu = invoker?.closest('#appearanceMenu'), section = menu?.querySelector('.appearance-pattern');
  const restoreMenu = menu?.matches(':popover-open'), menuScroll = menu?.scrollTop;
  const bubble = menu?.querySelector('.pattern-library'), bubbleScroll = bubble?.scrollTop;
  const expanded = section?.open;
  const dialog = document.createElement('dialog');
  dialog.id = 'contourEditor';
  dialog.className = 'desk-dialog contour-editor';
  dialog.setAttribute('aria-labelledby', 'contourEditorTitle');
  dialog.setAttribute('aria-describedby', 'contourEditorAvailability');
  dialog.innerHTML = `
    <header class="modal-head"><h2 id="contourEditorTitle">Создать свой узор</h2>
      <button type="button" class="action editor-close" aria-label="Закрыть редактор" data-editor-close>×</button></header>
    <div class="modal-body">
      <p id="contourEditorAvailability" class="editor-notice">Обработка фотографий, предпросмотр контура и сохранение пока недоступны. Можно загрузить и кадрировать исходник, настроить схему размещения.</p>
      <div class="editor-fields">
        <label for="contourEditorName">Название контура<input id="contourEditorName" maxlength="120" autocomplete="off" autofocus placeholder="Название нового контура"></label>
        <label for="contourEditorFile">Исходная фотография<input id="contourEditorFile" type="file" accept="image/jpeg,image/png,image/webp" aria-describedby="contourEditorFileHint"></label>
      </div>
      <p class="editor-help" id="contourEditorFileHint">JPG, PNG или WebP до 20 МБ. Фото остаётся только в памяти этой вкладки и не отправляется на сервер.</p>
      <p class="editor-error" id="contourEditorError" role="status" aria-live="polite"></p>
      <div class="editor-previews">
        <section class="editor-panel"><h3>Исходная фотография</h3>
          <div class="editor-photo-stage" aria-label="Выделение области фотографии" tabindex="0">
            <span class="editor-photo-empty">Загрузите фотографию</span><div class="editor-crop" hidden></div>
          </div>
          <p class="editor-help editor-photo-name" hidden></p>
          <p class="editor-help">Проведите мышью или пальцем по фото, чтобы выделить кадр. Координаты можно задать ниже в процентах.</p>
          <fieldset class="editor-crop-fields" disabled><legend>Кадрирование, %</legend>
            <label>X<input data-crop="x" type="number" min="0" max="99" step="1" value="0"></label>
            <label>Y<input data-crop="y" type="number" min="0" max="99" step="1" value="0"></label>
            <label>Ширина<input data-crop="width" type="number" min="1" max="100" step="1" value="100"></label>
            <label>Высота<input data-crop="height" type="number" min="1" max="100" step="1" value="100"></label>
          </fieldset>
          <button type="button" class="action" data-crop-reset disabled>Сбросить кадрирование</button>
        </section>
        <section class="editor-panel"><h3>Выделенный контур</h3>
          <div class="editor-unavailable" role="img" aria-label="Предпросмотр контура недоступен"><span>Предпросмотр недоступен</span><small>Алгоритм выделения контура ещё не подключён.</small></div>
        </section>
        <section class="editor-panel"><h3>Итоговая композиция</h3>
          <div class="editor-scheme"><svg viewBox="-150 -150 300 300" role="img" aria-label="Схема расположения условных рамок"></svg></div>
          <p class="editor-scheme-count" aria-live="polite"></p>
          <p class="editor-help">Только схема размещения, не результат обработки. Готовый предпросмотр пока недоступен.</p>
        </section>
      </div>
      <div class="editor-settings">
        <section class="editor-panel"><h3>Обработка <span class="editor-tag">Недоступна</span></h3>
          <p class="editor-help" id="contourProcessingHint">Настройки станут доступны после подключения алгоритма.</p>
          <fieldset class="editor-processing" disabled aria-describedby="contourProcessingHint"><legend class="editor-sr-only">Настройки обработки</legend>
            <label for="contourSensitivity">Чувствительность<input id="contourSensitivity" type="range" min="0" max="100" value="50"></label>
            <label for="contourDetail">Детализация<input id="contourDetail" type="range" min="0" max="100" value="50"></label>
            <label for="contourThickness">Толщина линий<input id="contourThickness" type="range" min="1" max="10" value="2"></label>
            <label class="editor-switch">Инверсия<input id="contourInvert" class="desk-toggle" type="checkbox" role="switch"></label>
            <label class="editor-switch">Очистка фона<input id="contourCleanup" class="desk-toggle" type="checkbox" role="switch" checked></label>
          </fieldset>
        </section>
        <section class="editor-panel"><h3>Композиция</h3>
          <div class="editor-composition">
            <label for="contourRotation">Поворот <output for="contourRotation">0°</output><input id="contourRotation" type="range" min="0" max="360" value="0"></label>
            <label class="editor-switch">Повторения<input id="contourRepeat" class="desk-toggle" type="checkbox" role="switch"></label>
            <label for="contourRings">Количество колец<select id="contourRings" disabled><option value="1">1 кольцо — 9 элементов</option><option value="2">2 кольца — 25 элементов</option></select></label>
            <label for="contourDistance">Расстояние между повторами <output for="contourDistance">20%</output><input id="contourDistance" type="range" min="0" max="200" value="20"></label>
            <label for="contourScale">Масштаб <output for="contourScale">100%</output><input id="contourScale" type="range" min="10" max="400" value="100"></label>
            <button type="button" class="action" data-editor-fit>Вписать в область</button>
          </div>
        </section>
      </div>
    </div>
    <footer class="modal-foot"><span class="editor-help">Сохранение будет доступно после подключения обработки.</span>
      <button type="button" class="action" data-editor-close>Отмена</button>
      <button type="button" class="action primary" disabled title="Сначала необходимо подключить алгоритм обработки" aria-describedby="contourEditorAvailability">Сохранить</button></footer>`;

  const find = selector => dialog.querySelector(selector);
  const stage = find('.editor-photo-stage'), cropBox = find('.editor-crop');
  const fileInput = find('#contourEditorFile'), error = find('#contourEditorError');
  const rotation = find('#contourRotation'), repeat = find('#contourRepeat'), rings = find('#contourRings');
  const distance = find('#contourDistance'), scale = find('#contourScale'), scheme = find('.editor-scheme svg');
  let dirty = false, closed = false, closing = false, loading = false, generation = 0;
  let photo = null, photoURL = null, crop = { x: 0, y: 0, width: 100, height: 100 }, drag = null;
  const urls = new Set(), pendingImages = new Set();

  function release(url) { if (urls.delete(url)) URL.revokeObjectURL(url); }
  function renderScheme() {
    const count = repeat.checked ? Number(rings.value) : 0;
    const zoom = Number(scale.value) / 100, step = 60 * (1 + Number(distance.value) / 100) * zoom;
    const frames = [];
    for (let y = -count; y <= count; y++) for (let x = -count; x <= count; x++) {
      frames.push(`<rect x="${-30 * zoom}" y="${-30 * zoom}" width="${60 * zoom}" height="${60 * zoom}" transform="translate(${x * step} ${y * step}) rotate(${rotation.value})"/>`);
    }
    scheme.innerHTML = frames.join('');
    scheme.setAttribute('aria-label', `Схема расположения: ${frames.length} условных рамок. Предпросмотр результата недоступен.`);
    find('.editor-scheme-count').textContent = `Схема размещения: ${frames.length} ${frames.length === 1 ? 'элемент' : 'элементов'}.`;
    for (const input of [rotation, distance, scale]) find(`output[for="${input.id}"]`).textContent = input.value + (input === rotation ? '°' : '%');
  }
  function fit() {
    const count = repeat.checked ? Number(rings.value) : 0;
    const radians = Number(rotation.value) * Math.PI / 180;
    const bounds = count * 2 * 60 * (1 + Number(distance.value) / 100) + 60 * (Math.abs(Math.cos(radians)) + Math.abs(Math.sin(radians)));
    scale.value = String(Math.max(10, Math.min(400, Math.floor(260 / bounds * 100))));
    renderScheme();
  }
  function photoBounds() {
    const factor = Math.min(stage.clientWidth / photo.naturalWidth, stage.clientHeight / photo.naturalHeight);
    const width = photo.naturalWidth * factor, height = photo.naturalHeight * factor;
    return { width, height, left: (stage.clientWidth - width) / 2, top: (stage.clientHeight - height) / 2 };
  }
  function renderCrop() {
    if (!photo) return;
    const bounds = photoBounds();
    Object.assign(cropBox.style, { left: `${bounds.left + bounds.width * crop.x / 100}px`, top: `${bounds.top + bounds.height * crop.y / 100}px`,
      width: `${bounds.width * crop.width / 100}px`, height: `${bounds.height * crop.height / 100}px` });
    for (const input of dialog.querySelectorAll('[data-crop]')) input.value = String(Math.round(crop[input.dataset.crop] * 10) / 10);
  }
  const observer = new ResizeObserver(renderCrop);
  observer.observe(stage);

  async function loadPhoto() {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    const token = ++generation;
    loading = true;
    error.textContent = 'Загрузка фотографии…';
    let candidate, url, adopted = false;
    try {
      if (file.size > MAX_BYTES) throw new Error('Фотография должна быть не больше 20 МБ.');
      const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
      const signature = String.fromCharCode(...bytes);
      const mime = signature.startsWith('\x89PNG\r\n\x1a\n') ? 'image/png' :
        bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 ? 'image/jpeg' :
        signature.startsWith('RIFF') && signature.slice(8) === 'WEBP' ? 'image/webp' : null;
      if (!mime) throw new Error('Поддерживаются только корректные JPG, PNG и WebP.');
      if (closed || token !== generation) return;
      url = URL.createObjectURL(new Blob([file], { type: mime }));
      urls.add(url);
      candidate = new Image();
      pendingImages.add(candidate);
      candidate.src = url;
      try { await candidate.decode(); } catch { throw new Error('Не удалось прочитать фотографию. Выберите корректный JPG, PNG или WebP.'); }
      if (closed || token !== generation) return;
      if (!candidate.naturalWidth || !candidate.naturalHeight) throw new Error('Не удалось прочитать фотографию.');
      if (photo) { photo.removeAttribute('src'); photo.remove(); release(photoURL); }
      photo = candidate; photoURL = url; adopted = true;
      photo.alt = 'Исходная фотография'; photo.draggable = false;
      stage.prepend(photo);
      find('.editor-photo-name').textContent = `${file.name} · ${photo.naturalWidth}×${photo.naturalHeight} px`;
      find('.editor-photo-name').hidden = false;
      find('.editor-photo-empty').hidden = true; cropBox.hidden = false;
      find('.editor-crop-fields').disabled = false; find('[data-crop-reset]').disabled = false;
      crop = { x: 0, y: 0, width: 100, height: 100 };
      renderCrop();
      dirty = true;
      error.textContent = '';
    } catch (failure) {
      if (!closed && token === generation) error.textContent = failure.message || 'Не удалось прочитать фотографию.';
    } finally {
      pendingImages.delete(candidate);
      if (!adopted) { if (candidate) candidate.removeAttribute('src'); if (url) release(url); }
      if (token === generation) loading = false;
    }
  }

  function cleanup() {
    closed = true; generation++;
    observer.disconnect();
    for (const image of pendingImages) image.removeAttribute('src');
    pendingImages.clear();
    if (photo) { photo.removeAttribute('src'); photo.remove(); }
    for (const url of [...urls]) release(url);
    photo = null; photoURL = null; crop = null; drag = null; loading = false; dirty = false;
    fileInput.value = ''; dialog.querySelectorAll('input').forEach(input => { if (input.type !== 'file') input.value = ''; });
    scheme.replaceChildren();
    find('.editor-photo-name').textContent = '';
  }
  async function requestClose() {
    if (closing || closed) return;
    closing = true;
    try {
      if ((dirty || loading) && !await window.HelpdeskDialogs.confirm('Есть несохранённые изменения. Закрыть редактор и удалить загруженную фотографию?',
        { title: 'Закрыть редактор?', acceptLabel: 'Закрыть без сохранения' })) return;
      cleanup(); dialog.close(); dialog.remove(); active = null;
      if (restoreMenu && menu?.isConnected) {
        menu.showPopover(); section.open = expanded;
        menu.scrollTop = menuScroll; bubble.scrollTop = bubbleScroll;
      }
      if (invoker?.isConnected) invoker.focus({ preventScroll: true });
    } finally { closing = false; }
  }

  fileInput.addEventListener('change', () => void loadPhoto());
  find('#contourEditorName').addEventListener('input', () => { dirty = true; });
  for (const input of [rotation, distance, scale]) input.addEventListener('input', () => { dirty = true; renderScheme(); });
  repeat.addEventListener('change', () => {
    dirty = true; rings.disabled = !repeat.checked;
    if (repeat.checked) { rings.value = '1'; fit(); } else renderScheme();
  });
  let previousRings = 1;
  rings.addEventListener('change', () => {
    dirty = true;
    const count = Number(rings.value);
    if (count > previousRings) fit(); else renderScheme();
    previousRings = count;
  });
  repeat.addEventListener('change', () => { previousRings = 1; });
  find('[data-editor-fit]').addEventListener('click', () => { dirty = true; fit(); });
  find('[data-crop-reset]').addEventListener('click', () => { dirty = true; crop = { x: 0, y: 0, width: 100, height: 100 }; renderCrop(); });
  for (const input of dialog.querySelectorAll('[data-crop]')) input.addEventListener('input', () => {
    if (!photo) return;
    const key = input.dataset.crop, value = Number(input.value);
    if (!Number.isFinite(value)) return;
    crop[key] = Math.min(key === 'x' || key === 'y' ? 99 : 100, Math.max(key === 'x' || key === 'y' ? 0 : 1, value));
    crop.width = Math.min(crop.width, 100 - crop.x); crop.height = Math.min(crop.height, 100 - crop.y);
    dirty = true; renderCrop();
  });
  function point(event) {
    const rect = stage.getBoundingClientRect(), bounds = photoBounds();
    return { x: Math.max(0, Math.min(100, (event.clientX - rect.left - stage.clientLeft - bounds.left) / bounds.width * 100)),
      y: Math.max(0, Math.min(100, (event.clientY - rect.top - stage.clientTop - bounds.top) / bounds.height * 100)) };
  }
  stage.addEventListener('pointerdown', event => {
    if (!photo || !event.isPrimary || event.button !== 0) return;
    event.preventDefault(); stage.setPointerCapture(event.pointerId);
    drag = { start: point(event), crop: { ...crop } };
  });
  stage.addEventListener('pointermove', event => {
    if (!drag || !photo) return;
    const end = point(event);
    crop = { x: Math.min(99, drag.start.x, end.x), y: Math.min(99, drag.start.y, end.y),
      width: Math.max(1, Math.abs(end.x - drag.start.x)), height: Math.max(1, Math.abs(end.y - drag.start.y)) };
    crop.width = Math.min(crop.width, 100 - crop.x); crop.height = Math.min(crop.height, 100 - crop.y);
    renderCrop();
  });
  stage.addEventListener('pointerup', () => { if (drag) { dirty = true; drag = null; } });
  stage.addEventListener('pointercancel', () => { if (drag) { crop = drag.crop; drag = null; renderCrop(); } });
  dialog.addEventListener('cancel', event => { event.preventDefault(); void requestClose(); });
  dialog.querySelectorAll('[data-editor-close]').forEach(button => button.addEventListener('click', () => void requestClose()));
  dialog.addEventListener('close', () => { if (!closed) { cleanup(); dialog.remove(); active = null; } });
  document.body.appendChild(dialog); active = dialog;
  renderScheme(); dialog.showModal();
  return dialog;
}
