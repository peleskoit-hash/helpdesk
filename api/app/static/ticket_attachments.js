(function initTicketAttachments(global) {
  const { headers, formatFileSize } = global.TicketApi || {};

  const attachmentObjectUrls = new Map();
  let stickerDepsPromise = null;
  const stickerLottieInstances = new Map();
  const stickerAnimationDataCache = new Map();
  const stickerVideoElements = new Map();
  let imagePreviewScale = 1;
  let imagePreviewAttachment = null;
  let imagePreviewRevision = 0;
  let avatarPreviewController = null;
  let avatarPreviewObjectUrl = null;
  let avatarPreviewTimer = null;

  function cancelImagePreview() {
    imagePreviewRevision += 1;
    avatarPreviewController?.abort();
    avatarPreviewController = null;
    clearTimeout(avatarPreviewTimer);
    avatarPreviewTimer = null;
    if (avatarPreviewObjectUrl) URL.revokeObjectURL(avatarPreviewObjectUrl);
    avatarPreviewObjectUrl = null;
    imagePreviewAttachment = null;
  }

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
      is_initial_task_description: Boolean(message.is_initial_task_description),
      is_initial_task_message: Boolean(message.is_initial_task_message),
      is_initial_task_assignment: Boolean(message.is_initial_task_assignment),
      message_type: message.message_type || "",
      message_status: message.message_status || "",
      status_note: message.status_note || "",
      created_at: message.created_at || "",
      replaced_message_id: message.replaced_message_id || "",
      quote_message_id: message.quote_message_id || "",
      author_name: message.author_name || "",
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
      const stale = (currentAttachmentIds && !currentAttachmentIds.has(value.attachmentId || attachmentId)) || !value?.container?.isConnected;
      if (!stale) continue;
      try {
        value.animation?.destroy?.();
      } catch {
        // noop
      }
      stickerLottieInstances.delete(attachmentId);
    }
    for (const [attachmentId, video] of stickerVideoElements.entries()) {
      const stale = (currentAttachmentIds && !currentAttachmentIds.has(video.dataset?.attachmentId || attachmentId)) || !video?.isConnected;
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

  async function renderTgsStickerInline(attachment, container, thumbnail = false) {
    await ensureStickerDeps();
    const resourceKey = thumbnail ? container : attachment.id;
    const existing = stickerLottieInstances.get(resourceKey);
    if (existing && existing.container === container) return;
    if (existing && existing.container !== container) {
      try {
        existing.animation?.destroy?.();
      } catch {
        // noop
      }
      stickerLottieInstances.delete(resourceKey);
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

    if (thumbnail && !container.isConnected) return;
    container.innerHTML = "";
    const animation = window.lottie.loadAnimation({
      container,
      renderer: "svg",
      loop: !thumbnail,
      autoplay: !thumbnail,
      animationData: thumbnail ? JSON.parse(JSON.stringify(animationData)) : animationData,
    });
    stickerLottieInstances.set(resourceKey, { attachmentId: attachment.id, container, animation });
  }

  function renderQuoteThumbnail(attachment, container) {
    const unavailable = () => {
      if (container.isConnected) container.textContent = "Миниатюра недоступна";
    };
    if (isTgsSticker(attachment)) {
      renderTgsStickerInline(attachment, container, true).catch(unavailable);
      return;
    }
    const media = document.createElement(isVideoSticker(attachment) ? "video" : "img");
    media.className = "quote-thumbnail";
    if (isVideoSticker(attachment)) {
      media.muted = true;
      media.playsInline = true;
      media.preload = "auto";
      media.dataset.attachmentId = attachment.id;
      stickerVideoElements.set(media, media);
    } else {
      media.alt = "Миниатюра цитаты";
    }
    media.addEventListener("error", unavailable, { once: true });
    container.appendChild(media);
    getAttachmentObjectUrl(attachment).then((url) => {
      if (media.isConnected) media.src = url;
    }).catch(unavailable);
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
    const open = document.createElement("a");
    open.className = "image-preview-open";
    open.title = "Открыть в новой вкладке";
    open.setAttribute("aria-label", open.title);
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    const iconFilter = document.createElement("span");
    // The supplied JPEG has a baked-in checkerboard: remove light pixels only at display time.
    iconFilter.innerHTML = '<svg width="0" height="0" aria-hidden="true" style="position:absolute"><defs><filter id="attachmentIconInk" color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 -3 -3 -3 0 1"/><feComposite in2="SourceAlpha" operator="in"/></filter></defs></svg>';
    open.innerHTML = '<svg viewBox="0 0 512 512" aria-hidden="true"><image width="512" height="512" href="/static/icons/image-open-original.png" filter="url(#attachmentIconInk)"/></svg>';
    download.innerHTML = '<svg viewBox="155 55 510 710" aria-hidden="true"><image width="820" height="820" href="/static/icons/image-download.jpg" filter="url(#attachmentIconInk)"/></svg>';
    download.setAttribute("aria-label", "Скачать");
    download.title = "Скачать";
    const img = document.createElement("img");
    img.className = "image-preview-img";
    img.alt = "";
    const loading = document.createElement("div");
    loading.className = "image-preview-avatar-loading hidden";
    loading.textContent = "Загрузка…";
    loading.setAttribute("role", "status");
    dialog.append(iconFilter, open, download, close, img, loading);
    modal.appendChild(dialog);
    document.body.appendChild(modal);

    function closePreview() {
      closeImagePreview(ctx);
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
    el.imagePreviewOpen = open;
    el.imagePreviewAvatarLoading = loading;
  }

  async function openImagePreview(ctx, attachment) {
    ensureImagePreviewModal(ctx);
    closeImagePreview(ctx);
    const revision = imagePreviewRevision;
    const { el } = ctx;
    try {
      const objectUrl = await getAttachmentObjectUrl(attachment);
      if (revision !== imagePreviewRevision) return;
      imagePreviewAttachment = attachment;
      imagePreviewScale = 1;
      el.imagePreviewOpen.href = objectUrl;
      el.imagePreviewImg.src = objectUrl;
      el.imagePreviewImg.alt = attachment.original_filename || "";
      el.imagePreviewImg.style.transform = "scale(1)";
      el.imagePreviewModal.classList.remove("hidden");
    } catch (e) {
      if (revision === imagePreviewRevision) ctx.showError(e.message);
    }
  }

  function closeImagePreview(ctx) {
    cancelImagePreview();
    const { el } = ctx;
    if (!el.imagePreviewModal) return;
    el.imagePreviewModal.classList.add("hidden");
    el.imagePreviewModal.classList.remove("avatar-preview", "avatar-preview-full");
    el.imagePreviewAvatarLoading?.classList.add("hidden");
    el.imagePreviewDownload?.classList.remove("hidden");
    el.imagePreviewOpen?.classList.remove("hidden");
    el.imagePreviewOpen?.removeAttribute("href");
    el.imagePreviewImg.removeAttribute("src");
  }

  async function openClientAvatarPreview(ctx, thumbnailUrl, fullUrl) {
    ensureImagePreviewModal(ctx);
    closeImagePreview(ctx);
    const { el } = ctx;
    const revision = imagePreviewRevision;
    const controller = new AbortController();
    avatarPreviewController = controller;
    imagePreviewScale = 1;
    el.imagePreviewImg.style.transform = "scale(1)";
    el.imagePreviewImg.alt = "Фотография клиента";
    el.imagePreviewImg.src = thumbnailUrl;
    el.imagePreviewDownload.classList.add("hidden");
    el.imagePreviewOpen.classList.add("hidden");
    el.imagePreviewModal.classList.add("avatar-preview");
    el.imagePreviewAvatarLoading.classList.remove("hidden");
    el.imagePreviewModal.classList.remove("hidden");
    avatarPreviewTimer = setTimeout(() => {
      controller.abort();
      if (revision === imagePreviewRevision) {
        el.imagePreviewAvatarLoading.classList.add("hidden");
        if (avatarPreviewObjectUrl && !el.imagePreviewModal.classList.contains("avatar-preview-full")) {
          URL.revokeObjectURL(avatarPreviewObjectUrl);
          avatarPreviewObjectUrl = null;
        }
      }
    }, 45000);
    let objectUrl = null;
    try {
      const response = await fetch(fullUrl, { headers: headers(), signal: controller.signal });
      if (!response.ok) return;
      const blob = await response.blob();
      if (revision !== imagePreviewRevision || controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      avatarPreviewObjectUrl = objectUrl;
      const image = new Image();
      image.src = objectUrl;
      await image.decode();
      if (revision !== imagePreviewRevision || controller.signal.aborted) return;
      el.imagePreviewImg.src = objectUrl;
      el.imagePreviewModal.classList.add("avatar-preview-full");
    } catch {
      // Keep the saved thumbnail on network, Telegram or image decoding failure.
      if (objectUrl && avatarPreviewObjectUrl === objectUrl) {
        URL.revokeObjectURL(objectUrl);
        avatarPreviewObjectUrl = null;
      }
    } finally {
      if (revision === imagePreviewRevision) {
        clearTimeout(avatarPreviewTimer);
        avatarPreviewTimer = null;
        el.imagePreviewAvatarLoading.classList.add("hidden");
      }
    }
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
    cancelImagePreview();
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
    renderQuoteThumbnail,
    openClientAvatarPreview,
    renderAttachments,
    cleanupAttachmentObjectUrls,
    cleanupStickerResources,
    isStickerOnlyMessage,
    messageRenderSignature,
    closeImagePreview,
    destroyAllAttachmentResources,
  };
})(window);
