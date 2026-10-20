const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element() {
  return {
    children: [], className: "", scrollHeight: 0, scrollTop: 0, clientHeight: 0,
    append(...items) { this.children.push(...items); },
    appendChild(item) { this.children.push(item); },
    handlers: {}, style: {}, classList: { add() {}, remove() {} },
    getBoundingClientRect: () => ({ width: 100, height: 100 }),
    addEventListener(type, handler) { this.handlers[type] = handler; },
    set innerHTML(value) { this.html = value; this.children = []; },
  };
}

function fixture(messages) {
  const window = { innerWidth: 1000, innerHeight: 1000 };
  const context = vm.createContext({ window, document: { createElement: element } });
  for (const name of ["ticket_attachments", "ticket_message_view"]) {
    vm.runInContext(fs.readFileSync(`api/app/static/${name}.js`, "utf8"), context);
  }
  const card = { messages, client_name: "Client", workflow_type: "task" };
  const container = element();
  const ctx = {
    state: { card, role: "admin" }, el: { messages: container, messageContextMenu: element() },
    quoteMessage: (message) => { ctx.quoted = message; },
    fmtMessageStamp: (value) => value || "date", linkifyText: (text) => text,
    isStickerOnlyMessage: () => false,
    messageRenderSignature: window.TicketAttachments.messageRenderSignature,
    cleanupAttachmentObjectUrls() {}, cleanupStickerResources() {},
    renderAttachments(message, target) {
      for (const file of message.attachments || []) {
        const node = element(); node.className = "file"; node.fileId = file.id;
        target.appendChild(node);
      }
    },
  };
  return { card, container, ctx, render: (signature = "") => window.TicketMessageView.renderMessages(ctx, card, signature, null) };
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
        is_initial_task_description: index === captionIndex, attachments: [{ id: `file-${index}` }],
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
      firstFile.handlers.contextmenu(event);
      view.ctx.el.messageContextMenu.children[1].handlers.click(event);
      assert.equal(view.ctx.quoted, initial[0]);
      const descriptionBubble = view.container.children[4];
      assert.equal(descriptionBubble.children[0].children[1].textContent, initial[captionIndex].created_at);
      descriptionBubble.handlers.contextmenu(event);
      view.ctx.el.messageContextMenu.children[1].handlers.click(event);
      assert.equal(view.ctx.quoted, initial[captionIndex]);
    });
  }
}

test("description flag change invalidates the render signature", () => {
  const message = { id: "first", message_type: "client", body: "Text", attachments: [{ id: "file" }] };
  const view = fixture([message]);
  const first = view.render();
  message.is_initial_task_description = true;
  const second = view.render(first.signature);
  assert.notEqual(first.signature, second.signature);
  assert.equal(view.container.children.length, 2);
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
