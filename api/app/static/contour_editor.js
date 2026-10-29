import { imageBounds, contourLayout, fitContourScale, contourMarkup } from '/static/contour_geometry.js?v=20261029-contour-spacing-v1';
import { prepareContourPNG } from '/static/contour_export.js?v=20261029-contour-geometry-v1';

const MAX_BYTES = 20 * 1024 * 1024;
let active = null, opening = null, styles = null;

function ensureStyles() {
  if (!styles) styles = new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/contour-editor.css?v=20261029-contour-aspect-v1';
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
    <header class="modal-head"><div class="editor-heading"><h2 id="contourEditorTitle">Создать свой узор</h2>
      <p id="contourEditorAvailability" class="editor-help">Обработка выполняется локально. В общую библиотеку отправляется только готовый прозрачный контур.</p></div>
      <button type="button" class="action editor-close" aria-label="Закрыть редактор" data-editor-close>×</button></header>
    <div class="modal-body">
      <div class="editor-fields">
        <label for="contourEditorName">Название контура<input id="contourEditorName" maxlength="120" autocomplete="off" autofocus placeholder="Название нового контура"></label>
        <label for="contourEditorFile">Исходная фотография<input id="contourEditorFile" type="file" accept="image/jpeg,image/png,image/webp" aria-describedby="contourEditorFileHint"></label>
      </div>
      <p class="editor-help" id="contourEditorFileHint">JPG, PNG или WebP до 20 МБ. Фото остаётся только в памяти этой вкладки и не отправляется на сервер.</p>
      <p class="editor-error" id="contourEditorError" role="status" aria-live="polite"></p>
      <div class="editor-previews">
        <section class="editor-panel"><h3>Оригинал</h3>
          <div class="editor-photo-stage" aria-label="Выделение области фотографии" tabindex="0">
            <span class="editor-photo-empty">Загрузите фотографию</span><div class="editor-crop" hidden></div>
          </div>
          <p class="editor-help editor-photo-name" hidden></p>
          <details class="editor-crop-section"><summary>Кадрирование</summary>
          <p class="editor-help">Проведите мышью или пальцем по фото, чтобы выделить кадр. Координаты можно задать ниже в процентах.</p>
          <fieldset class="editor-crop-fields" disabled><legend>Кадрирование, %</legend>
            <label>X<input data-crop="x" type="number" min="0" max="99" step="1" value="0"></label>
            <label>Y<input data-crop="y" type="number" min="0" max="99" step="1" value="0"></label>
            <label>Ширина<input data-crop="width" type="number" min="1" max="100" step="1" value="100"></label>
            <label>Высота<input data-crop="height" type="number" min="1" max="100" step="1" value="100"></label>
          </fieldset>
          <button type="button" class="action" data-crop-reset disabled>Сбросить кадрирование</button>
          </details>
        </section>
        <section class="editor-panel"><h3>Фоновый узор</h3>
          <div class="editor-scheme"><svg viewBox="-150 -150 300 300" role="img" aria-label="Предпросмотр композиции"></svg><span class="editor-result-empty">Загрузите фотографию для обработки.</span></div>
          <p class="editor-scheme-count" aria-live="polite"></p>
          <p class="editor-processing-status editor-help" role="status" aria-live="polite">Подготовка обработки…</p>
          <button type="button" class="action" data-process-retry hidden>Повторить обработку</button>
        </section>
      </div>
      <div class="editor-settings">
        <details class="editor-panel editor-processing-section"><summary>Обработка</summary>
          <p class="editor-help" id="contourProcessingHint">Инверсия меняет сторону границы. Очистка удаляет контуры, связанные с краями кадра.</p>
          <fieldset class="editor-processing" disabled aria-describedby="contourProcessingHint"><legend class="editor-sr-only">Настройки обработки</legend>
            <label for="contourSensitivity">Чувствительность<input id="contourSensitivity" type="range" min="0" max="100" value="50"></label>
            <label for="contourDetail">Детализация<input id="contourDetail" type="range" min="0" max="100" value="50"></label>
            <label for="contourThickness">Толщина линий<input id="contourThickness" type="range" min="1" max="10" value="2"></label>
            <label class="editor-switch">Инверсия<input id="contourInvert" class="desk-toggle" type="checkbox" role="switch"></label>
            <label class="editor-switch">Очистка фона<input id="contourCleanup" class="desk-toggle" type="checkbox" role="switch" checked></label>
          </fieldset>
        </details>
        <details class="editor-panel editor-composition-section"><summary>Композиция</summary>
          <div class="editor-composition">
            <label for="contourScale">Масштаб <output for="contourScale">100%</output><input id="contourScale" type="range" min="50" max="200" value="100"></label>
            <label for="contourRotation">Поворот <output for="contourRotation">0°</output><input id="contourRotation" type="range" min="0" max="360" value="0"></label>
            <label class="editor-switch">Повторения<input id="contourRepeat" class="desk-toggle" type="checkbox" role="switch"></label>
            <label for="contourRings">Количество колец<select id="contourRings" disabled><option value="1">1 кольцо — 9 элементов</option><option value="2">2 кольца — 25 элементов</option></select></label>
            <label for="contourDistance">Расстояние между повторами <output for="contourDistance">20%</output><input id="contourDistance" type="range" min="0" max="200" value="20"></label>
            <button type="button" class="action" data-editor-fit>Вписать в область</button>
          </div>
        </details>
      </div>
    </div>
    <footer class="modal-foot"><span class="editor-help">В библиотеке появится контур без исходной фотографии.</span>
      <button type="button" class="action" data-editor-close>Отмена</button>
      <button type="button" class="action primary" disabled data-editor-save title="Укажите название и дождитесь готового контура" aria-describedby="contourEditorAvailability">Сохранить</button></footer>`;

  const find = selector => dialog.querySelector(selector);
  const stage = find('.editor-photo-stage'), cropBox = find('.editor-crop');
  const fileInput = find('#contourEditorFile'), error = find('#contourEditorError');
  const rotation = find('#contourRotation'), repeat = find('#contourRepeat'), rings = find('#contourRings');
  const distance = find('#contourDistance'), scale = find('#contourScale'), scheme = find('.editor-scheme svg');
  const processing = find('.editor-processing'), processingStatus = find('.editor-processing-status');
  const resultEmpty = find('.editor-result-empty');
  const retryProcessing = find('[data-process-retry]');
  const nameInput = find('#contourEditorName'), saveButton = find('[data-editor-save]');
  let saving = false, pendingFit = false;
  let dirty = false, closed = false, closing = false, loading = false, generation = 0;
  let photo = null, photoURL = null, crop = { x: 0, y: 0, width: 100, height: 100 }, drag = null;
  const urls = new Set(), pendingImages = new Set();
  let sourceBlob = null, result = null, worker = null, workerReady = false, workerBusy = false, activeJob = null;
  let processingRevision = 0, processingTimer = null, processingWatchdog = null, pendingProcessing = false;

  function release(url) { if (urls.delete(url)) URL.revokeObjectURL(url); }
  function renderScheme() {
    const count = repeat.checked ? Number(rings.value) : 0;
    const area = { width: window.innerWidth, height: window.innerHeight };
    const composition = { rings: count, rotation: Number(rotation.value), distance: Number(distance.value), base_scale: Number(scale.value) };
    scheme.setAttribute('viewBox', `${-area.width / 2} ${-area.height / 2} ${area.width} ${area.height}`);
    const clip = `<defs><clipPath id="editorArea"><rect x="${-area.width / 2}" y="${-area.height / 2}" width="${area.width}" height="${area.height}"/></clipPath></defs>`;
    scheme.innerHTML = result ? `${clip}<g clip-path="url(#editorArea)">${contourMarkup(result, composition, contourLayout(result, composition, area), 'editorContour', result.url)}</g>` : '';
    const total = (count * 2 + 1) ** 2;
    scheme.setAttribute('aria-label', result ? `Композиция: ${total} элементов.` : 'Нет обработанного контура.');
    const label = result ? `Композиция: ${total} ${total === 1 ? 'элемент' : 'элементов'}.` : 'Нет обработанного контура.';
    if (find('.editor-scheme-count').textContent !== label) find('.editor-scheme-count').textContent = label;
    for (const input of [rotation, distance, scale]) find(`output[for="${input.id}"]`).textContent = input.value + (input === rotation ? '°' : '%');
  }
  function fit() {
    if (!result) { pendingFit = true; return; }
    pendingFit = false;
    const composition = { rings: repeat.checked ? Number(rings.value) : 0, rotation: Number(rotation.value), distance: Number(distance.value) };
    scale.value = String(fitContourScale(result, composition, { width: window.innerWidth, height: window.innerHeight }));
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
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(renderCrop) : null;
  observer?.observe(stage);
  function resizePreviews() {
    dialog.style.setProperty('--editor-preview-ratio', String(window.innerWidth / window.innerHeight));
    renderScheme(); renderCrop();
  }
  window.addEventListener('resize', resizePreviews);

  function clearResult(message = 'Обновление контура…') {
    if (result) release(result.url);
    result = null;
    resultEmpty.textContent = message; resultEmpty.hidden = false;
    renderScheme(); updateSave();
  }
  function stopWorker() {
    clearTimeout(processingWatchdog); processingWatchdog = null;
    if (worker) { worker.onmessage = worker.onerror = null; worker.terminate(); }
    worker = null; workerReady = false; workerBusy = false; activeJob = null;
  }
  function processingFailed(message) {
    stopWorker();
    clearTimeout(processingTimer); processingTimer = null; pendingProcessing = false;
    processingRevision++;
    clearResult('Обработка недоступна.');
    processing.disabled = true;
    processingStatus.textContent = message;
    retryProcessing.hidden = false;
  }
  function runProcessing() {
    processingTimer = null;
    if (closed || loading || !sourceBlob || !workerReady || workerBusy || !pendingProcessing) return;
    workerBusy = true; pendingProcessing = false;
    activeJob = processingRevision;
    const settings = {
      sensitivity: Number(find('#contourSensitivity').value), detail: Number(find('#contourDetail').value),
      thickness: Number(find('#contourThickness').value), invert: find('#contourInvert').checked, cleanup: find('#contourCleanup').checked,
    };
    processingStatus.textContent = 'Обработка фотографии…';
    processingWatchdog = setTimeout(() => processingFailed('Обработка не завершилась. Попробуйте ещё раз.'), 30000);
    try { worker.postMessage({ id: processingRevision, blob: sourceBlob, crop: { ...crop }, settings }); }
    catch { processingFailed('Не удалось передать фотографию в Worker.'); }
  }
  function scheduleProcessing() {
    processingRevision++;
    clearTimeout(processingTimer); processingTimer = null;
    clearResult(!workerReady ? 'Обработка ещё не готова.' : sourceBlob && !loading ? 'Обновление контура…' : 'Загрузите фотографию для обработки.');
    pendingProcessing = !!sourceBlob;
    if (!workerReady) return;
    processingTimer = setTimeout(runProcessing, 180);
  }
  function startWorker() {
    stopWorker();
    if (typeof Worker !== 'function' || typeof OffscreenCanvas !== 'function' || typeof createImageBitmap !== 'function' || typeof ImageData !== 'function') {
      processingFailed('Браузер не поддерживает необходимые Canvas и Web Worker API.'); return;
    }
    retryProcessing.hidden = true;
    processingStatus.textContent = 'Подготовка обработки…';
    processingWatchdog = setTimeout(() => processingFailed('Не удалось загрузить Worker. Попробуйте ещё раз.'), 10000);
    try {
      const instance = new Worker('/static/contour_worker.js?v=20261029-contour-geometry-v1', { type: 'module' });
      worker = instance;
      instance.onerror = event => { event.preventDefault(); if (!closed && worker === instance) processingFailed('Ошибка загрузки или выполнения Worker. Повторите обработку.'); };
      instance.addEventListener('messageerror', () => { if (!closed && worker === instance) processingFailed('Не удалось прочитать ответ Worker.'); });
      instance.onmessage = async event => {
        if (closed || worker !== instance) return;
        const message = event.data;
        if (!message || typeof message !== 'object') { processingFailed('Worker вернул некорректный ответ.'); return; }
        if (message.type === 'ready') {
          clearTimeout(processingWatchdog);
          if (!message.supported) { processingFailed('Worker не поддерживает необходимые Canvas API.'); return; }
          workerReady = true; processing.disabled = false;
          processingStatus.textContent = sourceBlob ? 'Обновление контура…' : 'Загрузите фотографию для обработки.';
          if (sourceBlob) scheduleProcessing();
          return;
        }
        if (message.id !== activeJob) return;
        clearTimeout(processingWatchdog); workerBusy = false; activeJob = null;
        if (message.id === processingRevision && !loading) {
          if (message.error) { processingFailed(message.error); return; }
          clearResult();
          if (message.count === 0) {
            resultEmpty.textContent = 'Контуры не найдены.';
            processingStatus.textContent = 'Контуры не найдены. Измените кадрирование или настройки.';
          } else if (message.blob instanceof Blob && message.blob.type === 'image/png' && Number.isInteger(message.count) && message.count > 0 &&
              message.width > 0 && message.height > 0 && message.count <= message.width * message.height && Math.max(message.width, message.height) <= 2048) {
            const url = URL.createObjectURL(message.blob); urls.add(url);
            const candidate = new Image(); pendingImages.add(candidate);
            let adopted = false;
            try {
              candidate.src = url; await candidate.decode();
              if (!closed && worker === instance && message.id === processingRevision && !loading) {
                if (candidate.naturalWidth !== message.width || candidate.naturalHeight !== message.height) throw new Error('Размер результата не совпадает.');
                result = { blob: message.blob, url, width: message.width, height: message.height, bounds: imageBounds(candidate) }; adopted = true;
                resultEmpty.hidden = true;
                processingStatus.textContent = `Готово: ${message.width}×${message.height} px. Прозрачный PNG, готов к сохранению.`;
                if (pendingFit) fit(); else renderScheme();
                updateSave();
              }
            } catch {
              if (!closed && worker === instance && message.id === processingRevision) processingFailed('Не удалось прочитать обработанный контур. Повторите обработку.');
            } finally { pendingImages.delete(candidate); candidate.removeAttribute('src'); if (!adopted) release(url); }
          } else { processingFailed('Worker вернул некорректный результат.'); return; }
        }
        if (pendingProcessing && !processingTimer) runProcessing();
      };
    } catch { processingFailed('Не удалось запустить Worker. Повторите обработку.'); }
  }

  async function loadPhoto() {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    const token = ++generation;
    loading = true;
    processingRevision++; pendingProcessing = false;
    clearTimeout(processingTimer); processingTimer = null;
    clearResult('Загрузка новой фотографии…');
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
      const blob = new Blob([file], { type: mime });
      url = URL.createObjectURL(blob);
      urls.add(url);
      candidate = new Image();
      pendingImages.add(candidate);
      candidate.src = url;
      try { await candidate.decode(); } catch { throw new Error('Не удалось прочитать фотографию. Выберите корректный JPG, PNG или WebP.'); }
      if (closed || token !== generation) return;
      if (!candidate.naturalWidth || !candidate.naturalHeight) throw new Error('Не удалось прочитать фотографию.');
      if (photo) { photo.removeAttribute('src'); photo.remove(); release(photoURL); }
      photo = candidate; photoURL = url; adopted = true;
      sourceBlob = blob;
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
      if (!closed && token === generation) {
        error.textContent = failure.message || 'Не удалось прочитать фотографию.';
        clearResult('Фото не обработано. Выберите корректный файл.');
        processingStatus.textContent = sourceBlob ? 'Можно повторить обработку прежнего исходника.' : 'Загрузите фотографию для обработки.';
        retryProcessing.hidden = !sourceBlob || !workerReady;
      }
    } finally {
      pendingImages.delete(candidate);
      if (!adopted) { if (candidate) candidate.removeAttribute('src'); if (url) release(url); }
      if (token === generation) loading = false;
      if (adopted && !closed && token === generation) scheduleProcessing();
    }
  }

  function cleanup() {
    closed = true; generation++;
    window.removeEventListener('beforeunload', warnBeforeUnload);
    clearTimeout(processingTimer); processingTimer = null; pendingProcessing = false;
    stopWorker();
    observer?.disconnect();
    window.removeEventListener('resize', resizePreviews);
    for (const image of pendingImages) image.removeAttribute('src');
    pendingImages.clear();
    if (photo) { photo.removeAttribute('src'); photo.remove(); }
    for (const url of [...urls]) release(url);
    photo = null; photoURL = null; sourceBlob = null; result = null; crop = null; drag = null; loading = false; dirty = false;
    fileInput.value = ''; dialog.querySelectorAll('input').forEach(input => { if (input.type !== 'file') input.value = ''; });
    scheme.replaceChildren();
    find('.editor-photo-name').textContent = '';
  }
  function finishClose() {
    cleanup(); dialog.close(); dialog.remove(); active = null;
    if (restoreMenu && menu?.isConnected) {
      menu.showPopover(); section.open = expanded;
      menu.scrollTop = menuScroll; bubble.scrollTop = bubbleScroll;
    }
    if (invoker?.isConnected) invoker.focus({ preventScroll: true });
  }
  async function requestClose() {
    if (closing || closed) return;
    if (saving) { error.textContent = 'Сохранение ещё выполняется. Дождитесь ответа сервера перед закрытием редактора.'; error.scrollIntoView({ block: 'nearest' }); return; }
    closing = true;
    try {
      if ((dirty || loading) && !await window.HelpdeskDialogs.confirm('Есть несохранённые изменения. Закрыть редактор и удалить загруженную фотографию?',
        { title: 'Закрыть редактор?', acceptLabel: 'Закрыть без сохранения' })) return;
      finishClose();
    } finally { closing = false; }
  }
  function updateSave() {
    saveButton.disabled = closed || saving || !nameInput.value.trim() || !result;
    saveButton.title = saveButton.disabled ? 'Укажите название и дождитесь готового контура' : 'Сохранить контур в общую библиотеку';
  }
  function warnBeforeUnload(event) { event.preventDefault(); event.returnValue = ''; }
  async function saveContour() {
    if (saving || closing || closed || !result || !nameInput.value.trim()) return;
    saving = true; drag = null; error.textContent = '';
    const controls = [...dialog.querySelectorAll('input, select, button:not([data-editor-close])')].map(element => [element, element.disabled]);
    controls.forEach(([element]) => { element.disabled = true; });
    saveButton.textContent = 'Сохранение…'; dialog.setAttribute('aria-busy', 'true');
    window.addEventListener('beforeunload', warnBeforeUnload);
    try {
      const prepared = await prepareContourPNG(result.blob);
      const data = new FormData();
      data.append('name', nameInput.value.trim());
      data.append('file', prepared.blob, 'contour.png');
      data.append('rotation', rotation.value);
      data.append('rings', repeat.checked ? rings.value : '0');
      data.append('distance', distance.value);
      data.append('base_scale', scale.value);
      const response = await fetch('/api/v1/background-contours', { method: 'POST', credentials: 'include', body: data })
        .catch(() => { throw new Error('Сетевая ошибка: сохранение не подтверждено. Проверьте библиотеку перед повторной попыткой.'); });
      let payload;
      try { payload = await response.json(); } catch { /* A proxy may return a non-JSON error. */ }
      if (!response.ok) throw new Error(payload?.detail?.error?.message || `Не удалось сохранить контур (HTTP ${response.status}).`);
      if (response.status !== 201 || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(payload?.id || '')) throw new Error('Сервер не подтвердил сохранение контура. Проверьте библиотеку перед повторной попыткой.');
      finishClose();
      document.querySelector('#appearanceMenu .appearance-pattern')?.dispatchEvent(new Event('contour-created'));
    } catch (failure) {
      if (!closed) { error.textContent = failure.message || 'Не удалось сохранить контур. Попробуйте ещё раз.'; error.scrollIntoView({ block: 'nearest' }); }
    } finally {
      window.removeEventListener('beforeunload', warnBeforeUnload);
      saving = false;
      if (!closed) {
        controls.forEach(([element, disabled]) => { element.disabled = disabled; });
        saveButton.textContent = 'Сохранить'; dialog.removeAttribute('aria-busy'); updateSave();
      }
    }
  }

  fileInput.addEventListener('change', () => void loadPhoto());
  nameInput.addEventListener('input', () => { dirty = true; updateSave(); });
  saveButton.addEventListener('click', () => void saveContour());
  for (const input of [rotation, distance, scale]) input.addEventListener('input', () => { dirty = true; pendingFit = false; renderScheme(); });
  repeat.addEventListener('change', () => {
    dirty = true; rings.disabled = !repeat.checked;
    if (repeat.checked) { rings.value = '1'; fit(); } else { pendingFit = false; renderScheme(); }
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
  find('[data-crop-reset]').addEventListener('click', () => { dirty = true; crop = { x: 0, y: 0, width: 100, height: 100 }; renderCrop(); scheduleProcessing(); });
  for (const input of dialog.querySelectorAll('[data-crop]')) input.addEventListener('input', () => {
    if (!photo) return;
    const key = input.dataset.crop, value = Number(input.value);
    if (!Number.isFinite(value)) return;
    crop[key] = Math.min(key === 'x' || key === 'y' ? 99 : 100, Math.max(key === 'x' || key === 'y' ? 0 : 1, value));
    crop.width = Math.min(crop.width, 100 - crop.x); crop.height = Math.min(crop.height, 100 - crop.y);
    dirty = true; renderCrop(); scheduleProcessing();
  });
  function point(event) {
    const rect = stage.getBoundingClientRect(), bounds = photoBounds();
    return { x: Math.max(0, Math.min(100, (event.clientX - rect.left - stage.clientLeft - bounds.left) / bounds.width * 100)),
      y: Math.max(0, Math.min(100, (event.clientY - rect.top - stage.clientTop - bounds.top) / bounds.height * 100)) };
  }
  stage.addEventListener('pointerdown', event => {
    if (saving || !photo || !event.isPrimary || event.button !== 0) return;
    event.preventDefault(); stage.setPointerCapture(event.pointerId);
    drag = { start: point(event), crop: { ...crop } };
  });
  stage.addEventListener('pointermove', event => {
    if (!drag || !photo) return;
    const end = point(event);
    crop = { x: Math.min(99, drag.start.x, end.x), y: Math.min(99, drag.start.y, end.y),
      width: Math.max(1, Math.abs(end.x - drag.start.x)), height: Math.max(1, Math.abs(end.y - drag.start.y)) };
    crop.width = Math.min(crop.width, 100 - crop.x); crop.height = Math.min(crop.height, 100 - crop.y);
    renderCrop(); scheduleProcessing();
  });
  stage.addEventListener('pointerup', () => { if (drag) { dirty = true; drag = null; } });
  stage.addEventListener('pointercancel', () => { if (drag) { crop = drag.crop; drag = null; renderCrop(); scheduleProcessing(); } });
  processing.addEventListener('input', () => { dirty = true; scheduleProcessing(); });
  retryProcessing.addEventListener('click', startWorker);
  dialog.addEventListener('cancel', event => { event.preventDefault(); void requestClose(); });
  dialog.querySelectorAll('[data-editor-close]').forEach(button => button.addEventListener('click', () => void requestClose()));
  dialog.addEventListener('close', () => { if (!closed) { cleanup(); dialog.remove(); active = null; } });
  document.body.appendChild(dialog); active = dialog;
  resizePreviews(); dialog.showModal(); startWorker();
  return dialog;
}
