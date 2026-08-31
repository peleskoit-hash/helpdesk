(function initTicketAttachments(global) {
  const { headers, formatFileSize } = global.TicketApi || {};

  const attachmentObjectUrls = new Map();
  let stickerDepsPromise = null;
  const stickerLottieInstances = new Map();
  const stickerAnimationDataCache = new Map();
  const stickerVideoElements = new Map();
  let imagePreviewScale = 1;
  let imagePreviewAttachment = null;

  function attachmentMeta(attachment) {
    return formatFileSize(attachment.file_size);
  }

  function attachmentExt(attachment) {
    const name = (attachment?.original_filename || "").toLowerCase();
    const idx = name.lastIndexOf(".");
    return idx >= 0 ? name.slice(idx) : "";
  }

  function isStickerAttachment(attachment) {
    const mime = (attachment?.mime_type || "").toLowerCase();
    const ext = attachmentExt(attachment);
    return mime.includes("sticker") || ext === ".tgs" || ext === ".webm" || ext === ".webp";
  }

  function isStickerOnlyMessage(message) {
    const attachments = message?.attachments || [];
    if (!attachments.length) return false;
    const body = (message?.body || "").trim();
    if (body) return false;
    return attachments.every((attachment) => isStickerAttachment(attachment));
  }

  function messageRenderSignature(messages) {
    return JSON.stringify((messages || []).map((message) => ({
      id: message.id,
      body: message.body || "",
      message_type: message.message_type || "",
      message_status: message.message_status || "",
      status_note: message.status_note || "",
      created_at: message.created_at || "",
      replaced_message_id: message.replaced_message_id || "",
      attachments: (message.attachments || []).map((attachment) => ({
        id: attachment.id,
        mime_type: attachment.mime_type || "",
        original_filename: attachment.original_filename || "",
        file_size: attachment.file_size || 0,
        telegram_delivery_status: attachment.telegram_delivery_status || "",
        download_url: attachment.download_url || "",
      })),
    })));
  }

  function deliveryStatusLabel(attachment) {
    const status = attachment?.telegram_delivery_status || "";
    if (status === "uploading") return "Загружается в Telegram...";
    if (status === "sending") return "Отправляется клиенту...";
    if (status === "failed") return "Не удалось отправить";
    return "";
  }

  function cleanupAttachmentObjectUrls(currentAttachmentIds) {
    for (const [attachmentId, objectUrl] of attachmentObjectUrls.entries()) {
      if (currentAttachmentIds.has(attachmentId)) continue;
      try {
        URL.revokeObjectURL(objectUrl);
      } catch {
        // noop
      }
      attachmentObjectUrls.delete(attachmentId);
    }
  }

  function cleanupStickerResources(currentAttachmentIds = new Set()) {
    for (const [attachmentId, value] of stickerLottieInstances.entries()) {
      const stale = !currentAttachmentIds.has(attachmentId) || !value?.container?.isConnected;
      if (!stale) continue;
      try {
        value.animation?.destroy?.();
      } catch {
        // noop
      }
      stickerLottieInstances.delete(attachmentId);
    }
    for (const [attachmentId, video] of stickerVideoElements.entries()) {
      const stale = !currentAttachmentIds.has(attachmentId) || !video?.isConnected;
      if (!stale) continue;
      try {
        video.pause();
        video.removeAttribute("src");
        video.load();
      } catch {
        // noop
      }
      stickerVideoElements.delete(attachmentId);
    }
  }

  function isTgsSticker(attachment) {
    const mime = (attachment?.mime_type || "").toLowerCase();
    const ext = attachmentExt(attachment);
    return mime.includes("x-tgsticker") || ext === ".tgs";
  }

  function isVideoSticker(attachment) {
    const mime = (attachment?.mime_type || "").toLowerCase();
    const ext = attachmentExt(attachment);
    return mime === "video/webm" || ext === ".webm";
  }

  function isStaticSticker(attachment) {
    const mime = (attachment?.mime_type || "").toLowerCase();
    const ext = attachmentExt(attachment);
    return mime === "image/webp" || ext === ".webp";
  }

  function loadScriptOnce(src, check) {
    if (check()) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Не удалось загрузить ${src}`));
      document.head.appendChild(script);
    });
  }

  function ensureStickerDeps() {
    if (stickerDepsPromise) return stickerDepsPromise;
    stickerDepsPromise = (async () => {
      await loadScriptOnce(
        "https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.min.js",
        () => typeof window.pako !== "undefined",
      );
      await loadScriptOnce(
        "https://cdn.jsdelivr.net/npm/lottie-web@5.12.2/build/player/lottie.min.js",
        () => typeof window.lottie !== "undefined",
      );
    })();
    return stickerDepsPromise;
  }

  async function getAttachmentObjectUrl(attachment) {
    if (attachmentObjectUrls.has(attachment.id)) {
      return attachmentObjectUrls.get(attachment.id);
    }
    const response = await fetch(attachment.download_url, {
      headers: headers(),
    });
    if (!response.ok) {
      const raw = await response.text();
      let message = raw || "Не удалось загрузить файл";
      try {
        const payload = JSON.parse(raw);
        message = payload?.detail?.error?.message || message;
      } catch {
        // Server may return plain text for unexpected errors.
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    attachmentObjectUrls.set(attachment.id, objectUrl);
    return objectUrl;
  }

  async function downloadAttachment(ctx, attachment) {
    try {
      const link = document.createElement("a");
      link.href = attachment.download_url;
      link.download = attachment.original_filename || "file";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      ctx.setStatus?.("Скачивание начато...");
    } catch (e) {
      ctx.showError(e.message);
    }
  }

  async function renderTgsStickerInline(attachment, container) {
    await ensureStickerDeps();
    const existing = stickerLottieInstances.get(attachment.id);
    if (existing && existing.container === container) return;
    if (existing && existing.container !== container) {
      try {
        existing.animation?.destroy?.();
      } catch {
        // noop
      }
      stickerLottieInstances.delete(attachment.id);
    }

    let animationData = stickerAnimationDataCache.get(attachment.id);
    if (!animationData) {
      const response = await fetch(attachment.download_url, { headers: headers() });
      if (!response.ok) throw new Error("Не удалось загрузить .tgs стикер");
      const bytes = new Uint8Array(await response.arrayBuffer());
      const jsonText = window.pako.ungzip(bytes, { to: "string" });
      animationData = JSON.parse(jsonText);
      stickerAnimationDataCache.set(attachment.id, animationData);
    }

    container.innerHTML = "";
    const animation = window.lottie.loadAnimation({
      container,
      renderer: "svg",
      loop: true,
      autoplay: true,
      animationData,
    });
    stickerLottieInstances.set(attachment.id, { container, animation });
  }

  function ensureImagePreviewModal(ctx) {
    const { el, closeOnBackdropClick } = ctx;
    if (el.imagePreviewModal) return;

    const modal = document.createElement("div");
    modal.className = "image-preview-modal hidden";
    const dialog = document.createElement("div");
    dialog.className = "image-preview-dialog";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "image-preview-close";
    close.textContent = "×";
    close.title = "Закрыть";
    const download = document.createElement("button");
    download.type = "button";
    download.className = "image-preview-download";
    download.textContent = "↓";
    download.title = "Скачать";
    const img = document.createElement("img");
    img.className = "image-preview-img";
    img.alt = "";
    dialog.append(download, close, img);
    modal.appendChild(dialog);
    document.body.appendChild(modal);

    function closePreview() {
      modal.classList.add("hidden");
      img.removeAttribute("src");
      imagePreviewAttachment = null;
    }

    close.addEventListener("click", closePreview);
    download.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (imagePreviewAttachment) {
        await downloadAttachment(ctx, imagePreviewAttachment);
      }
    });
    closeOnBackdropClick(modal, closePreview);
    modal.addEventListener("wheel", (event) => {
      event.preventDefault();
      imagePreviewScale += event.deltaY < 0 ? 0.12 : -0.12;
      imagePreviewScale = Math.min(4, Math.max(0.25, imagePreviewScale));
      img.style.transform = `scale(${imagePreviewScale})`;
    }, { passive: false });

    el.imagePreviewModal = modal;
    el.imagePreviewImg = img;
    el.imagePreviewClose = close;
    el.imagePreviewDownload = download;
  }

  async function openImagePreview(ctx, attachment) {
    ensureImagePreviewModal(ctx);
    const { el } = ctx;
    try {
      const objectUrl = await getAttachmentObjectUrl(attachment);
      imagePreviewAttachment = attachment;
      imagePreviewScale = 1;
      el.imagePreviewImg.src = objectUrl;
      el.imagePreviewImg.alt = attachment.original_filename || "";
      el.imagePreviewImg.style.transform = "scale(1)";
      el.imagePreviewModal.classList.remove("hidden");
    } catch (e) {
      ctx.showError(e.message);
    }
  }

  function closeImagePreview(ctx) {
    const { el } = ctx;
    if (!el.imagePreviewModal) return;
    el.imagePreviewModal.classList.add("hidden");
    el.imagePreviewImg.removeAttribute("src");
  }

  function renderAttachments(ctx, message, container) {
    if ((message?.message_status || "") === "deleted") return;
    const attachments = message.attachments || [];
    if (!attachments.length) return;

    const wrap = document.createElement("div");
    wrap.className = "message-attachments";
    for (const attachment of attachments) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = `attachment-block ${attachment.is_image ? "attachment-image" : "attachment-file"}`;
      const name = document.createElement("div");
      name.className = "attachment-name";
      name.textContent = attachment.original_filename || "Файл";
      const meta = document.createElement("div");
      meta.className = "attachment-meta";
      meta.textContent = attachmentMeta(attachment);
      const deliveryStatusText = deliveryStatusLabel(attachment);
      const deliveryStatus = deliveryStatusText ? document.createElement("div") : null;
      if (deliveryStatus) {
        deliveryStatus.className = `attachment-delivery-status attachment-delivery-status-${attachment.telegram_delivery_status || "unknown"}`;
        deliveryStatus.textContent = deliveryStatusText;
      }

      if (isStickerAttachment(attachment)) {
        item.classList.add("attachment-sticker");
        const media = document.createElement("div");
        media.className = "sticker-preview attachment-sticker-media";
        const metaSticker = document.createElement("div");
        metaSticker.className = "attachment-meta";
        metaSticker.textContent = attachmentMeta(attachment);

        if (isStaticSticker(attachment)) {
          const img = document.createElement("img");
          img.alt = "sticker";
          img.loading = "lazy";
          img.className = "sticker-attachment attachment-sticker-image";
          getAttachmentObjectUrl(attachment)
            .then((objectUrl) => {
              if (img.isConnected) img.src = objectUrl;
            })
            .catch(() => {
              if (!img.isConnected) return;
              media.innerHTML = "<div class='attachment-unsupported-text'>Стикер недоступен</div>";
            });
          media.appendChild(img);
        } else if (isVideoSticker(attachment)) {
          const video = document.createElement("video");
          video.className = "sticker-video attachment-sticker-video";
          video.autoplay = true;
          video.loop = true;
          video.muted = true;
          video.playsInline = true;
          getAttachmentObjectUrl(attachment)
            .then((objectUrl) => {
              if (video.isConnected) {
                video.src = objectUrl;
                stickerVideoElements.set(attachment.id, video);
              }
            })
            .catch(() => {
              if (!video.isConnected) return;
              media.innerHTML = "<div class='attachment-unsupported-text'>Стикер недоступен</div>";
            });
          media.appendChild(video);
        } else if (isTgsSticker(attachment)) {
          const host = document.createElement("div");
          host.className = "sticker-lottie attachment-sticker-lottie";
          media.appendChild(host);
          renderTgsStickerInline(attachment, host).catch(() => {
            if (!host.isConnected) return;
            host.innerHTML = "";
            const unsupported = document.createElement("div");
            unsupported.className = "attachment-unsupported-text";
            unsupported.textContent = "Анимированные стикеры не поддерживаются";
            host.appendChild(unsupported);
          });
        }

        item.append(media, metaSticker);
        if (deliveryStatus) item.appendChild(deliveryStatus);
        item.addEventListener("click", () => downloadAttachment(ctx, attachment));
      } else if (attachment.is_unsupported_preview) {
        item.classList.add("attachment-unsupported");
        const unsupported = document.createElement("div");
        unsupported.className = "attachment-unsupported-text";
        unsupported.textContent = attachment.unsupported_preview_text || "Предпросмотр не поддерживается";
        item.append(unsupported, meta);
        if (deliveryStatus) item.appendChild(deliveryStatus);
        item.addEventListener("click", () => downloadAttachment(ctx, attachment));
      } else if (attachment.is_image) {
        const img = document.createElement("img");
        img.alt = attachment.original_filename || "";
        img.loading = "lazy";
        img.className = "attachment-image-preview";
        if (attachmentObjectUrls.has(attachment.id)) {
          img.src = attachmentObjectUrls.get(attachment.id);
        } else {
          getAttachmentObjectUrl(attachment)
            .then((objectUrl) => {
              if (img.isConnected) img.src = objectUrl;
            })
            .catch(() => {
              if (!img.isConnected) return;
              item.classList.remove("attachment-image");
              item.classList.add("attachment-file");
              img.remove();
            });
        }
        img.addEventListener("error", () => {
          item.classList.remove("attachment-image");
          item.classList.add("attachment-file");
          img.remove();
        }, { once: true });
        item.append(img, meta);
        if (deliveryStatus) item.appendChild(deliveryStatus);
        item.addEventListener("click", () => openImagePreview(ctx, attachment));
      } else {
        item.append(name, meta);
        if (deliveryStatus) item.appendChild(deliveryStatus);
        item.addEventListener("click", () => downloadAttachment(ctx, attachment));
      }
      wrap.appendChild(item);
    }
    container.appendChild(wrap);
  }

  function destroyAllAttachmentResources() {
    cleanupAttachmentObjectUrls(new Set());
    cleanupStickerResources(new Set());
    for (const [, data] of stickerLottieInstances.entries()) {
      try {
        data.animation?.destroy?.();
      } catch {
        // noop
      }
    }
    stickerLottieInstances.clear();
    stickerAnimationDataCache.clear();
  }

  global.TicketAttachments = {
    renderAttachments,
    cleanupAttachmentObjectUrls,
    cleanupStickerResources,
    isStickerOnlyMessage,
    messageRenderSignature,
    closeImagePreview,
    destroyAllAttachmentResources,
  };
})(window);
