import { getHistoryFile } from './api.js?v=20261102-web-preloads-v1';

const imageTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);

export function renderHistoryFile(container, ticketId, file, signal, download) {
  const row = document.createElement('div');
  row.className = 'history-file';
  const name = document.createElement('span');
  name.textContent = file.filename;
  const size = document.createElement('small');
  size.textContent = file.size === null ? 'Размер неизвестен' : file.size < 1024 ? `${file.size} Б`
    : file.size < 1024 ** 2 ? `${(file.size / 1024).toFixed(1)} КБ` : `${(file.size / 1024 ** 2).toFixed(1)} МБ`;
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = `Скачать: ${file.filename}`;
  const status = document.createElement('div');
  status.setAttribute('role', 'status');
  button.addEventListener('click', () => download(file, button, status));
  row.append(name, size, button, status);
  container.append(row);

  const sticker = file.source === 'sticker';
  if (!(sticker && ['image/webp', 'application/x-tgsticker', 'video/webm'].includes(file.mime_type))
      && !(!sticker && imageTypes.has(file.mime_type))) return;
  const preview = document.createElement('div');
  preview.className = sticker ? 'history-sticker' : 'history-image';
  row.prepend(preview);
  let objectUrl, zoom, image;
  const cleanup = () => {
    zoom?.close();
    zoom?.remove();
    image?.removeAttribute('src');
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  };
  signal.addEventListener('abort', cleanup, { once: true });
  async function load() {
    try {
      const bytes = await getHistoryFile(ticketId, file, signal);
      if (signal.aborted || !preview.isConnected) return;
      const blob = new Blob([bytes], { type: file.mime_type });
      if (sticker) {
        await window.TicketAttachments.renderStickerBlob({ source: 'sticker', mime_type: file.mime_type,
          original_filename: file.filename }, blob, preview, signal);
        return;
      }
      objectUrl = URL.createObjectURL(blob);
      const enlarge = document.createElement('button');
      enlarge.type = 'button';
      enlarge.setAttribute('aria-label', `Увеличить: ${file.filename}`);
      image = document.createElement('img');
      image.alt = file.filename;
      image.src = objectUrl;
      image.addEventListener('error', () => { preview.textContent = 'Предпросмотр недоступен'; }, { once: true });
      enlarge.append(image);
      enlarge.addEventListener('click', () => {
        if (signal.aborted) return;
        if (!zoom) {
          zoom = document.createElement('dialog');
          zoom.className = 'history-image-dialog';
          zoom.setAttribute('aria-label', file.filename);
          const close = document.createElement('button');
          close.type = 'button';
          close.textContent = 'Закрыть изображение';
          close.addEventListener('click', () => zoom.close());
          const full = document.createElement('img');
          full.src = objectUrl;
          full.alt = file.filename;
          zoom.append(close, full);
          container.closest('#session-panel').append(zoom);
        }
        zoom.showModal();
      });
      preview.append(enlarge);
    } catch {
      if (!signal.aborted && preview.isConnected) preview.textContent = 'Не удалось загрузить предпросмотр. Файл можно скачать.';
    }
  }
  void load();
}
