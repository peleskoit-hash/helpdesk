const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element() {
  return {
    children: [], dataset: {}, className: "", scrollHeight: 0, scrollTop: 0, clientHeight: 0,
    append(...items) { this.children.push(...items); },
    appendChild(item) { this.children.push(item); },
    querySelector(selector) { return this.children.find((child) => `.${child.className}` === selector); },
    contains(node) { return node === this; },
    handlers: {}, style: {}, classList: { add() {}, remove() {} },
    getBoundingClientRect: () => ({ width: 100, height: 100 }),
    addEventListener(type, handler) { this.handlers[type] = handler; },
    set innerHTML(value) { this.html = value; this.children = []; },
  };
}

function fixture(messages) {
  const window = { innerWidth: 1000, innerHeight: 1000, getSelection: () => null };
  const navigator = { clipboard: { async writeText(text) { ctx.copied = text; } } };
  const context = vm.createContext({ window, document: { createElement: element }, navigator });
  for (const name of ["ticket_attachments", "ticket_message_view"]) {
    vm.runInContext(fs.readFileSync(`api/app/static/${name}.js`, "utf8"), context);
  }
  const card = { messages, client_name: "Client", workflow_type: "task" };
  const container = element();
  const ctx = {
    state: { card, role: "admin" }, el: { messages: container, messageContextMenu: element() },
    quoteMessage: (message) => { ctx.quoted = message; },
    setStatus() {},
    fmtMessageStamp: (value) => value || "date", linkifyText: (text) => text,
    isStickerOnlyMessage: () => false,
    stickerType: window.TicketAttachments.stickerType,
    isPhotoAttachment: window.TicketAttachments.isPhotoAttachment,
    isPdfAttachment: window.TicketAttachments.isPdfAttachment,
    downloadAttachment: (attachment) => { ctx.attachmentAction = ["download", attachment]; },
    copyAttachmentImage: (attachment, kind) => { ctx.attachmentAction = ["copy", attachment, kind]; },
    openAttachmentInTab: (attachment, kind) => { ctx.attachmentAction = ["open", attachment, kind]; },
    messageRenderSignature: window.TicketAttachments.messageRenderSignature,
    cleanupAttachmentObjectUrls() {}, cleanupStickerResources() {},
    renderAttachments(message, target) {
      for (const file of message.attachments || []) {
        const node = element(); node.className = "file"; node.fileId = file.id;
        target.appendChild(node);
      }
    },
  };
  return { card, container, ctx, window, render: (signature = "") => window.TicketMessageView.renderMessages(ctx, card, signature, null) };
}

for (const [fileCount, captionIndex] of [[4, 0], [4, 2], [1, 0]]) {
  test(`initial description is separate once: ${fileCount} files, caption ${captionIndex + 1}`, () => {
    const messages = Array.from({ length: fileCount }, (_, index) => ({
      id: `message-${index}`, message_type: "client", body: index === captionIndex ? "Description" : "",
      is_initial_task_description: index === captionIndex, attachments: [{ id: `file-${index}` }],
    }));
    const original = JSON.stringify(messages);
    const { container, render } = fixture(messages);
    const result = render();
    assert.equal(container.children.length, fileCount + 1);
    const texts = container.children.filter((bubble) => bubble.children.some((child) => child.className === "message-body"));
    assert.equal(texts.length, 1);
    assert.equal(texts[0].children.find((child) => child.className === "message-body").html, "Description");
    assert.equal(texts[0].children.some((child) => child.className === "file"), false);
    assert.deepEqual(container.children.flatMap((bubble) => bubble.children.filter((child) => child.className === "file").map((file) => file.fileId)),
      Array.from({ length: fileCount }, (_, index) => `file-${index}`));
    render(result.signature);
    assert.equal(container.children.length, fileCount + 1);
    assert.equal(JSON.stringify(messages), original);
  });
}

test("later task messages, ordinary tickets and employee replies remain combined", () => {
  for (const messageType of ["client", "agent", "internal"]) {
    for (const workflow of ["task", "ticket"]) {
      const view = fixture([{ id: "later", message_type: messageType, body: "Text", attachments: [{ id: "file" }] }]);
      view.card.workflow_type = workflow;
      view.render();
      assert.equal(view.container.children.length, 1);
      assert.equal(view.container.children[0].children.filter((child) => ["message-body", "file"].includes(child.className)).length, 2);
    }
  }
});

for (const captionIndex of [0, 3]) {
  for (const assigned of [false, true]) {
    test(`initial files precede description and assignment: caption ${captionIndex + 1}, assigned ${assigned}`, () => {
      const initial = Array.from({ length: 4 }, (_, index) => ({
        id: `initial-${index}`, message_type: "client", created_at: `original-date-${index}`,
        body: index === captionIndex ? "Description" : "", is_initial_task_message: true,
        is_initial_task_description: index === captionIndex,
        attachments: [{ id: `file-${index}`, download_url: `/original/${index}` }],
      }));
      const assignment = { id: "assignment", message_type: "system", body: "Assigned", is_initial_task_assignment: true };
      const unrelated = { id: "unrelated", message_type: "system", body: "Unrelated" };
      const later = { id: "later", message_type: "client", body: "Later text", attachments: [{ id: "later-file" }] };
      const reassignment = { id: "reassignment", message_type: "system", body: "Reassigned" };
      const messages = [initial[0], ...(assigned ? [assignment] : []), unrelated, ...initial.slice(1), later, reassignment];
      const original = JSON.stringify(messages);
      const view = fixture(messages);
      view.render();
      const order = view.container.children.flatMap((bubble) => bubble.children
        .filter((child) => child.className === "file" || child.className === "message-body")
        .map((child) => child.fileId || child.html));
      assert.deepEqual(order, ["file-0", "file-1", "file-2", "file-3", "Description",
        ...(assigned ? ["Assigned"] : []), "Unrelated", "Later text", "later-file", "Reassigned"]);
      assert.equal(JSON.stringify(messages), original);
      const firstFile = view.container.children[0];
      assert.equal(firstFile.children[0].children[1].textContent, initial[0].created_at);
      const event = { preventDefault() {}, stopPropagation() {}, clientX: 10, clientY: 10 };
      const { messageContextMenu } = view.ctx.el;
      const menuLabels = () => messageContextMenu.children.map((button) => button.textContent);
      const quote = () => messageContextMenu.children.find((button) => button.textContent === "Цитировать").handlers.click(event);
      const fileWithCaption = view.container.children[captionIndex];
      fileWithCaption.handlers.contextmenu({ ...event, currentTarget: fileWithCaption });
      assert.deepEqual(menuLabels(), ["Скачать файл", "Цитировать"]);
      quote();
      assert.equal(view.ctx.quoted, initial[captionIndex]);
      const descriptionBubble = view.container.children[4];
      assert.equal(descriptionBubble.children[0].children[1].textContent, initial[captionIndex].created_at);
      descriptionBubble.handlers.contextmenu({ ...event, currentTarget: descriptionBubble });
      assert.deepEqual(menuLabels(), ["Копировать сообщение", "Цитировать"]);
      quote();
      assert.equal(view.ctx.quoted, initial[captionIndex]);
      descriptionBubble.handlers.contextmenu({ ...event, currentTarget: descriptionBubble });
      messageContextMenu.children.find((button) => button.textContent === "Копировать сообщение").handlers.click(event);
      assert.equal(view.ctx.copied, initial[captionIndex].body);
    });
  }
}

test("attachment menus use the displayed bubble and the original attachment references", () => {
  const photo = { id: "photo", is_image: true, mime_type: "image/webp", original_filename: "original.webp", download_url: "/original/photo" };
  const otherPhoto = { ...photo, id: "another-photo", download_url: "/original/another-photo" };
  const file = { id: "file", mime_type: "text/plain", download_url: "/original/file" };
  const pdf = { id: "pdf", mime_type: "application/pdf", download_url: "/original/pdf" };
  const sticker = { ...photo, source: "sticker", original_filename: "custom.webp" };
  const legacySticker = { ...photo, original_filename: "sticker.webp" };
  const namedPhoto = { ...legacySticker, source: "document" };
  const webm = { id: "webm", source: "sticker", mime_type: "video/webm", download_url: "/original/webm" };
  const tgs = { id: "tgs", source: "sticker", mime_type: "application/x-tgsticker", download_url: "/original/tgs" };
  const unsupported = { ...photo, is_unsupported_preview: true };
  const [downloadPhoto, copyPhoto, openPhoto, downloadFile, quote, copyText, copySelected] =
    ["Скачать фото", "Скопировать фото в буфер", "Открыть фото в новом окне", "Скачать файл",
      "Цитировать", "Копировать сообщение", "Копировать выделенное"];
  const photoMenu = [downloadPhoto, copyPhoto, openPhoto, quote];
  const [downloadSticker, copySticker, openSticker, openPdf] =
    ["Скачать стикер", "Скопировать стикер в буфер", "Открыть в новой вкладке", "Открыть PDF в новой вкладке"];

  for (const { body = "", attachments, split, deleted, selected, menus } of [
    { attachments: [photo], menus: [photoMenu] },
    { attachments: [namedPhoto], menus: [photoMenu] },
    { body: "Caption", attachments: [photo], menus: [[copyText, ...photoMenu]] },
    { body: "Caption", attachments: [photo], selected: "apt", menus: [[copySelected, ...photoMenu]] },
    { body: "Caption", attachments: [photo], split: true, menus: [photoMenu, [copyText, quote]] },
    { attachments: [file], menus: [[downloadFile, quote]] },
    { body: "Caption", attachments: [file], menus: [[copyText, downloadFile, quote]] },
    { body: "Caption", attachments: [photo, otherPhoto], menus: [[copyText, quote]] },
    { body: "Caption", attachments: [photo, otherPhoto], split: true, menus: [[quote], [quote], [copyText, quote]] },
    { attachments: [pdf], menus: [[downloadFile, openPdf, quote]] },
    { body: "Caption", attachments: [pdf], menus: [[copyText, downloadFile, openPdf, quote]] },
    { attachments: [sticker], menus: [[downloadSticker, copySticker, openSticker, quote]] },
    { attachments: [legacySticker], menus: [[downloadSticker, copySticker, openSticker, quote]] },
    { attachments: [webm], menus: [[downloadSticker, openSticker, quote]] },
    { attachments: [{ ...webm, source: "video" }], menus: [[downloadFile, quote]] },
    { attachments: [{ ...webm, source: undefined }], menus: [[downloadSticker, openSticker, quote]] },
    { attachments: [tgs], menus: [[downloadSticker, quote]] },
    { attachments: [{ ...tgs, source: null }], menus: [[downloadSticker, quote]] },
    { body: "Caption", attachments: [photo, pdf, webm, file], menus: [[copyText, quote]] },
    { body: "Caption", attachments: [photo, pdf, webm, file], split: true, menus: [[quote], [quote], [quote], [downloadFile, quote], [copyText, quote]] },
    { attachments: [unsupported], menus: [[downloadFile, quote]] },
    { attachments: [photo], deleted: true, menus: [[quote]] },
  ]) {
    const message = { id: "original", message_type: "client", body, attachments,
      is_initial_task_message: split, is_initial_task_description: split,
      message_status: deleted ? "deleted" : "normal" };
    const { container, ctx, window, render } = fixture([message]);
    render();
    assert.equal(container.children.length, menus.length);
    for (const [index, bubble] of container.children.entries()) {
      const event = { currentTarget: bubble, preventDefault() {}, stopPropagation() {}, clientX: 10, clientY: 10 };
      const openMenu = () => bubble.handlers.contextmenu(event);
      const click = (label) => ctx.el.messageContextMenu.children.find((button) => button.textContent === label).handlers.click(event);
      if (selected) {
        const bodyNode = bubble.querySelector(".message-body");
        window.getSelection = () => ({
          rangeCount: 1, getRangeAt: () => ({ commonAncestorContainer: bodyNode }), toString: () => selected,
        });
      }
      openMenu();
      assert.deepEqual(ctx.el.messageContextMenu.children.map((button) => button.textContent), menus[index]);
      click(quote);
      assert.equal(ctx.quoted, message);
      for (const [label, action, kind] of [
        [downloadFile, "download"], [downloadPhoto, "download"], [downloadSticker, "download"],
        [copyPhoto, "copy", "фото"], [openPhoto, "open", "фото"], [openPdf, "open", "PDF"],
        [copySticker, "copy", "стикер"], [openSticker, "open", "стикер"],
      ]) {
        if (!menus[index].includes(label)) continue;
        openMenu();
        click(label);
        assert.equal(ctx.attachmentAction[0], action);
        assert.equal(ctx.attachmentAction[1], attachments.find(({ id }) => id === bubble.children.find((node) => node.className === "file").fileId));
        assert.equal(ctx.attachmentAction[2], kind);
      }
    }
  }
});

test("description flag change invalidates the render signature", () => {
  const message = { id: "first", message_type: "client", body: "Text", attachments: [{ id: "file" }] };
  const view = fixture([message]);
  const first = view.render();
  message.is_initial_task_description = true;
  const second = view.render(first.signature);
  assert.notEqual(first.signature, second.signature);
  assert.equal(view.container.children.length, 2);
});

test("attachment source change invalidates the render signature and updates the menu", () => {
  const attachment = { id: "file", original_filename: "sticker.webp", mime_type: "image/webp",
    is_image: true, download_url: "/original/file" };
  const view = fixture([{ id: "message", message_type: "client", attachments: [attachment] }]);
  const labels = () => {
    const currentTarget = view.container.children[0];
    currentTarget.handlers.contextmenu({ currentTarget, preventDefault() {}, stopPropagation() {} });
    return view.ctx.el.messageContextMenu.children.map((button) => button.textContent);
  };
  const first = view.render();
  assert.ok(labels().includes("Скачать стикер"));
  attachment.source = "document";
  const second = view.render(first.signature);
  assert.notEqual(first.signature, second.signature);
  assert.ok(labels().includes("Скачать фото"));
});

for (const assigned of [false, true]) {
  test(`incoming album is visible on each refresh without waiting: assigned ${assigned}`, () => {
    const view = fixture([]);
    let signature = "";
    for (let index = 0; index < 4; index += 1) {
      view.card.messages.push({
        id: `incoming-${index}`, message_type: "client", is_initial_task_message: true,
        body: index === 3 ? "Description" : "", is_initial_task_description: index === 3,
        attachments: [{ id: `file-${index}` }],
      });
      if (assigned && index === 0) view.card.messages.push({
        id: "assignment", message_type: "system", body: "Assigned", is_initial_task_assignment: true,
      });
      signature = view.render(signature).signature;
      const order = view.container.children.flatMap((bubble) => bubble.children
        .filter((child) => child.className === "file" || child.className === "message-body")
        .map((child) => child.fileId || child.html));
      assert.deepEqual(order, [
        ...Array.from({ length: index + 1 }, (_, fileIndex) => `file-${fileIndex}`),
        ...(index === 3 ? ["Description"] : []), ...(assigned ? ["Assigned"] : []),
      ]);
    }
  });
}
