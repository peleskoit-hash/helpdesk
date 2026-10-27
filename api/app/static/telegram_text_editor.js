(function initTelegramTextEditor(global) {
  const Editor = {};
  const EMOJI_PICKER_ID = "telegramTextEmojiPicker";
  const ALLOWED_TAGS = new Set(["b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "tg-spoiler", "code", "pre", "blockquote", "a"]);
  const FALLBACK_EMOJI_CATEGORIES = [
    { id: "smileys", label: "Смайлы", emojis: ["🙂", "😀", "😁", "😂", "😊", "😍", "😎", "😢", "😡", "👍"] },
    { id: "gestures", label: "Жесты", emojis: ["👍", "👎", "👏", "🙏", "👌", "✌️", "🤝", "💪", "👋", "☝️"] },
    { id: "symbols", label: "Символы", emojis: ["✅", "❌", "⚠️", "ℹ️", "⭐", "🔥", "💬", "📌", "➡️", "⬅️"] },
  ];
  let emojiOutsideClickHandler = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function sanitizeLinkHref(rawHref) {
    const href = String(rawHref || "").trim();
    if (!href) return "";
    try {
      const parsed = new URL(href, window.location.origin);
      const protocol = String(parsed.protocol || "").toLowerCase();
      if (!["http:", "https:", "tg:", "mailto:"].includes(protocol)) return "";
      return href;
    } catch (_err) {
      return "";
    }
  }

  function validateTelegramHtml(text) {
    const stack = [];
    const re = /<\s*(\/)?\s*([a-zA-Z][a-zA-Z0-9-]*)([^<>]*)>/g;
    let match;
    while ((match = re.exec(String(text || ""))) !== null) {
      const closing = !!match[1];
      const tag = String(match[2] || "").toLowerCase();
      const attrs = String(match[3] || "").trim();
      const fragment = match[0];
      if (!ALLOWED_TAGS.has(tag)) return `Неподдерживаемый Telegram-тег ${fragment}`;
      const normalized = tag === "strong" ? "b" : (tag === "em" ? "i" : (tag === "ins" ? "u" : (tag === "strike" || tag === "del" ? "s" : tag)));
      if (closing) {
        if (!stack.length) return `Закрывающий Telegram-тег </${tag}> без открывающего`;
        const opened = stack.pop();
        if (opened.tag !== normalized) return `Некорректное пересечение Telegram-тегов: ${opened.fragment} закрывается </${tag}>`;
        continue;
      }
      if (attrs.endsWith("/")) return `Самозакрывающийся Telegram-тег не поддерживается: ${fragment}`;
      if (tag === "a") {
        const hrefMatch = attrs.match(/^href\s*=\s*("([^"]+)"|'([^']+)')$/i);
        const href = hrefMatch ? (hrefMatch[2] || hrefMatch[3] || "") : "";
        if (!sanitizeLinkHref(href)) return `Некорректная ссылка в ${fragment}`;
      } else if (tag === "blockquote") {
        if (attrs && !/^expandable(?:\s*=\s*(?:""|'')\s*)?$/i.test(attrs)) return `Запрещённый атрибут в ${fragment}`;
      } else if (attrs) {
        return `Запрещённый атрибут в ${fragment}`;
      }
      stack.push({ tag: normalized, fragment });
    }
    if (stack.length) return `Незакрытый Telegram-тег ${stack[stack.length - 1].fragment}`;
    return "";
  }

  function renderTelegramPreviewHtml(text) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(`<div>${String(text || "")}</div>`, "text/html");
    const root = doc.body.firstElementChild || doc.body;
    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || "");
      if (node.nodeType !== Node.ELEMENT_NODE) return document.createTextNode("");
      const tag = String(node.tagName || "").toLowerCase();
      if (!ALLOWED_TAGS.has(tag)) {
        const fragment = document.createDocumentFragment();
        node.childNodes.forEach((child) => fragment.appendChild(walk(child)));
        return fragment;
      }
      let out;
      if (tag === "tg-spoiler") {
        out = document.createElement("span");
        out.className = "tg-preview-spoiler";
      } else if (tag === "strong") out = document.createElement("b");
      else if (tag === "em") out = document.createElement("i");
      else if (tag === "ins") out = document.createElement("u");
      else if (tag === "strike" || tag === "del") out = document.createElement("s");
      else out = document.createElement(tag);
      if (tag === "a") {
        const href = sanitizeLinkHref(node.getAttribute("href") || "");
        if (href) {
          out.setAttribute("href", href);
          out.setAttribute("target", "_blank");
          out.setAttribute("rel", "noopener noreferrer");
        }
      }
      if (tag === "blockquote" && node.hasAttribute("expandable")) out.classList.add("tg-preview-expandable");
      node.childNodes.forEach((child) => out.appendChild(walk(child)));
      return out;
    }
    const previewRoot = document.createElement("div");
    root.childNodes.forEach((child) => previewRoot.appendChild(walk(child)));
    return previewRoot.innerHTML || "Текст не задан";
  }

  function getEmojiCategories() {
    const source = Array.isArray(global.BotButtonsEmojiData) && global.BotButtonsEmojiData.length
      ? global.BotButtonsEmojiData
      : FALLBACK_EMOJI_CATEGORIES;
    return source.map((category, index) => ({
      id: String(category.id || `emoji-${index}`),
      label: String(category.label || category.title || `Категория ${index + 1}`),
      emojis: Array.isArray(category.emojis) ? category.emojis : (Array.isArray(category.items) ? category.items : []),
    })).filter((category) => category.emojis.length);
  }

  function closeEmojiPicker() {
    const picker = document.getElementById(EMOJI_PICKER_ID);
    if (picker) picker.remove();
    if (emojiOutsideClickHandler) {
      document.removeEventListener("mousedown", emojiOutsideClickHandler, true);
      emojiOutsideClickHandler = null;
    }
    document.querySelectorAll("[data-format-action='emoji']").forEach((btn) => btn.setAttribute("aria-expanded", "false"));
  }

  function normalizedAtomicTags(tags) {
    const unique = new Set((Array.isArray(tags) ? tags : [])
      .map((tag) => String(tag || ""))
      .filter(Boolean));
    return [...unique].sort((a, b) => b.length - a.length);
  }

  function findAtomicTagRanges(text, tags) {
    const source = String(text || "");
    const ranges = [];
    normalizedAtomicTags(tags).forEach((tag) => {
      let fromIndex = 0;
      while (fromIndex <= source.length) {
        const index = source.indexOf(tag, fromIndex);
        if (index === -1) break;
        const end = index + tag.length;
        if (!ranges.some((range) => index < range.end && end > range.start)) {
          ranges.push({ start: index, end, text: tag });
        }
        fromIndex = Math.max(end, index + 1);
      }
    });
    return ranges.sort((a, b) => a.start - b.start || b.end - a.end);
  }

  function rangeTouches(range, start, end) {
    if (start === end) return start > range.start && start < range.end;
    return start < range.end && end > range.start;
  }

  function expandSelectionToAtomicTags(text, tags, start, end) {
    let nextStart = Math.max(0, Math.min(Number(start || 0), String(text || "").length));
    let nextEnd = Math.max(nextStart, Math.min(Number(end ?? nextStart), String(text || "").length));
    let changed = true;
    while (changed) {
      changed = false;
      findAtomicTagRanges(text, tags).forEach((range) => {
        if (!rangeTouches(range, nextStart, nextEnd)) return;
        if (range.start < nextStart) {
          nextStart = range.start;
          changed = true;
        }
        if (range.end > nextEnd) {
          nextEnd = range.end;
          changed = true;
        }
      });
    }
    return { start: nextStart, end: nextEnd };
  }

  function snapCaretToAtomicBoundary(text, tags, position, direction = "nearest") {
    const source = String(text || "");
    const pos = Math.max(0, Math.min(Number(position || 0), source.length));
    const range = findAtomicTagRanges(source, tags).find((item) => pos > item.start && pos < item.end);
    if (!range) return pos;
    if (direction === "left") return range.start;
    if (direction === "right") return range.end;
    return (pos - range.start) <= (range.end - pos) ? range.start : range.end;
  }

  function atomicKeyEdit(text, tags, start, end, key) {
    const source = String(text || "");
    let range = expandSelectionToAtomicTags(source, tags, start, end);
    if (range.start === range.end && key === "Backspace") {
      const previous = findAtomicTagRanges(source, tags).find((item) => item.end === range.start);
      if (previous) range = { start: previous.start, end: previous.end };
    }
    if (range.start === range.end && key === "Delete") {
      const next = findAtomicTagRanges(source, tags).find((item) => item.start === range.start);
      if (next) range = { start: next.start, end: next.end };
    }
    return range;
  }

  function replaceTextRange(textarea, text, range, selectionMode = "end") {
    const source = textarea.value || "";
    textarea.focus();
    textarea.setSelectionRange(range.start, range.end);
    if (document.execCommand && document.execCommand("insertText", false, text)) return;
    if (textarea.setRangeText) {
      textarea.setRangeText(text, range.start, range.end, selectionMode);
    } else {
      textarea.value = source.slice(0, range.start) + text + source.slice(range.end);
      const cursor = range.start + text.length;
      if (selectionMode === "select") textarea.setSelectionRange(range.start, cursor);
      else textarea.setSelectionRange(cursor, cursor);
    }
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function atomicReplaceText(textarea, text, range) {
    replaceTextRange(textarea, text, range, "end");
  }

  function renderEmojiPickerHtml() {
    const categories = getEmojiCategories();
    return `
      <div id="${EMOJI_PICKER_ID}" class="bot-emoji-picker" role="dialog" aria-label="Emoji">
        <div class="bot-emoji-tabs" role="tablist" aria-label="Категории emoji">
          ${categories.map((category, index) => `<button type="button" class="bot-emoji-tab ${index === 0 ? "active" : ""}" data-emoji-category-index="${index}">${escapeHtml(category.label)}</button>`).join("")}
        </div>
        <div class="bot-emoji-scroll">
          ${categories.map((category, index) => `
            <section class="bot-emoji-category" data-emoji-category="${index}">
              <div class="bot-emoji-category-title">${escapeHtml(category.label)}</div>
              <div class="bot-emoji-grid">
                ${category.emojis.map((emoji) => `<button type="button" class="bot-emoji-item" data-emoji="${escapeHtml(emoji)}" aria-label="${escapeHtml(emoji)}">${escapeHtml(emoji)}</button>`).join("")}
              </div>
            </section>
          `).join("")}
        </div>
      </div>
    `;
  }

  function applyTagToSelection(textarea, before, after = "") {
    if (!textarea) return;
    const source = textarea.value || "";
    const atomicRange = expandSelectionToAtomicTags(
      source,
      textarea.__telegramAtomicTags || [],
      textarea.selectionStart ?? 0,
      textarea.selectionEnd ?? 0,
    );
    const start = atomicRange.start;
    const end = atomicRange.end;
    const selected = source.slice(start, end);
    replaceTextRange(textarea, before + selected + after, { start, end }, selected ? "select" : "end");
    if (selected) {
      textarea.setSelectionRange(start + before.length, start + before.length + selected.length);
    }
  }

  function insertTextAtSelection(textarea, text, range = null) {
    if (!textarea || !text) return;
    const source = textarea.value || "";
    const rawStart = Math.max(0, Math.min(range?.start ?? textarea.selectionStart ?? 0, source.length));
    const rawEnd = Math.max(rawStart, Math.min(range?.end ?? textarea.selectionEnd ?? rawStart, source.length));
    const atomicRange = expandSelectionToAtomicTags(source, textarea.__telegramAtomicTags || [], rawStart, rawEnd);
    const start = snapCaretToAtomicBoundary(source, textarea.__telegramAtomicTags || [], atomicRange.start);
    const end = snapCaretToAtomicBoundary(source, textarea.__telegramAtomicTags || [], atomicRange.end);
    replaceTextRange(textarea, text, { start, end }, "end");
  }

  function stripAllowedTelegramTags(text) {
    return String(text || "").replace(/<\/?(?:b|strong|i|em|u|ins|s|strike|del|tg-spoiler|code|pre|blockquote|a)(?:\s+[^>]*)?>/gi, "");
  }

  function bindAtomicTokens(textarea, tags = []) {
    if (!textarea) return;
    textarea.__telegramAtomicTags = normalizedAtomicTags(tags);
    if (textarea.dataset.telegramAtomicBound === "1") return;
    textarea.dataset.telegramAtomicBound = "1";

    const snapCurrentCaret = () => {
      if ((textarea.selectionStart ?? 0) !== (textarea.selectionEnd ?? 0)) return;
      const source = textarea.value || "";
      const current = textarea.selectionStart ?? 0;
      const snapped = snapCaretToAtomicBoundary(source, textarea.__telegramAtomicTags || [], current);
      if (snapped !== current) textarea.setSelectionRange(snapped, snapped);
    };

    textarea.addEventListener("keydown", (event) => {
      const source = textarea.value || "";
      const tagsForInput = textarea.__telegramAtomicTags || [];
      const start = textarea.selectionStart ?? 0;
      const end = textarea.selectionEnd ?? start;
      if (event.key === "Backspace" || event.key === "Delete") {
        const range = atomicKeyEdit(source, tagsForInput, start, end, event.key);
        if (range.start !== start || range.end !== end) {
          event.preventDefault();
          atomicReplaceText(textarea, "", range);
        }
        return;
      }
      if (event.key === "ArrowLeft" && start === end) {
        const previous = findAtomicTagRanges(source, tagsForInput).find((range) => range.end === start);
        if (previous) {
          event.preventDefault();
          textarea.setSelectionRange(previous.start, previous.start);
        }
        return;
      }
      if (event.key === "ArrowRight" && start === end) {
        const next = findAtomicTagRanges(source, tagsForInput).find((range) => range.start === start);
        if (next) {
          event.preventDefault();
          textarea.setSelectionRange(next.end, next.end);
        }
      }
    });
    textarea.addEventListener("beforeinput", (event) => {
      const source = textarea.value || "";
      const tagsForInput = textarea.__telegramAtomicTags || [];
      const start = textarea.selectionStart ?? 0;
      const end = textarea.selectionEnd ?? start;
      const inputType = String(event.inputType || "");
      if (inputType === "deleteContentBackward" || inputType === "deleteContentForward") {
        const range = atomicKeyEdit(source, tagsForInput, start, end, inputType === "deleteContentBackward" ? "Backspace" : "Delete");
        if (range.start !== start || range.end !== end) {
          event.preventDefault();
          atomicReplaceText(textarea, "", range);
        }
        return;
      }
      if (!inputType.startsWith("insert")) return;
      const range = expandSelectionToAtomicTags(source, tagsForInput, start, end);
      const snappedStart = snapCaretToAtomicBoundary(source, tagsForInput, range.start);
      const snappedEnd = snapCaretToAtomicBoundary(source, tagsForInput, range.end);
      if (snappedStart === start && snappedEnd === end && range.start === start && range.end === end) return;
      event.preventDefault();
      const inserted = event.dataTransfer?.getData?.("text/plain") ?? event.data ?? "";
      atomicReplaceText(textarea, String(inserted), { start: snappedStart, end: snappedEnd });
    });
    textarea.addEventListener("click", snapCurrentCaret);
    textarea.addEventListener("mouseup", snapCurrentCaret);
    textarea.addEventListener("keyup", snapCurrentCaret);
  }

  function toolbarHtml() {
    return `
      <div class="bot-text-toolbar" data-telegram-toolbar>
        <button type="button" class="action" data-format-action="bold"><b>Ж</b></button>
        <button type="button" class="action" data-format-action="italic"><i>К</i></button>
        <button type="button" class="action" data-format-action="underline"><u>Ч</u></button>
        <button type="button" class="action" data-format-action="strike"><s>З</s></button>
        <button type="button" class="action" data-format-action="spoiler">Спойлер</button>
        <button type="button" class="action" data-format-action="code">Код</button>
        <button type="button" class="action" data-format-action="pre">Блок кода</button>
        <button type="button" class="action" data-format-action="quote">Цитата</button>
        <button type="button" class="action" data-format-action="quote_expandable">Под кат</button>
        <button type="button" class="action" data-format-action="link">Ссылка</button>
        <button type="button" class="action bot-emoji-trigger" data-format-action="emoji" aria-expanded="false" aria-haspopup="dialog">🙂</button>
        <button type="button" class="action" data-format-action="clear">Очистить формат</button>
      </div>
    `;
  }

  function bindToolbar(root, textarea, options = {}) {
    const toolbar = root.querySelector("[data-telegram-toolbar]");
    if (!toolbar || !textarea) return;
    if (Array.isArray(options.tags)) {
      textarea.__telegramAtomicTags = normalizedAtomicTags(options.tags);
    }
    let lastSelection = { start: textarea.selectionStart ?? 0, end: textarea.selectionEnd ?? 0 };
    const rememberSelection = () => {
      lastSelection = { start: textarea.selectionStart ?? 0, end: textarea.selectionEnd ?? 0 };
    };
    textarea.addEventListener("input", rememberSelection);
    textarea.addEventListener("keyup", rememberSelection);
    textarea.addEventListener("click", rememberSelection);
    textarea.addEventListener("select", rememberSelection);
    toolbar.addEventListener("mousedown", (event) => {
      if (event.target?.closest?.("[data-format-action], .bot-emoji-picker")) event.preventDefault();
    });
    toolbar.querySelectorAll("[data-format-action]").forEach((button) => {
      button.addEventListener("click", async () => {
        const action = button.getAttribute("data-format-action");
        if (action !== "emoji") closeEmojiPicker();
        if (action === "emoji") {
          if (document.getElementById(EMOJI_PICKER_ID)) {
            closeEmojiPicker();
            textarea.focus();
            return;
          }
          toolbar.insertAdjacentHTML("beforeend", renderEmojiPickerHtml());
          button.setAttribute("aria-expanded", "true");
          const picker = document.getElementById(EMOJI_PICKER_ID);
          emojiOutsideClickHandler = (event) => {
            const activePicker = document.getElementById(EMOJI_PICKER_ID);
            if (!activePicker) return;
            if (activePicker.contains(event.target) || button.contains(event.target)) return;
            closeEmojiPicker();
          };
          document.addEventListener("mousedown", emojiOutsideClickHandler, true);
          picker?.addEventListener("click", (event) => {
            const tab = event.target?.closest?.("[data-emoji-category-index]");
            if (tab) {
              const idx = Number(tab.getAttribute("data-emoji-category-index"));
              const target = picker.querySelector(`[data-emoji-category="${idx}"]`);
              const scrollRoot = picker.querySelector(".bot-emoji-scroll");
              picker.querySelectorAll(".bot-emoji-tab").forEach((btn) => btn.classList.remove("active"));
              tab.classList.add("active");
              if (target && scrollRoot) scrollRoot.scrollTo({ top: target.offsetTop - scrollRoot.offsetTop, behavior: "smooth" });
              return;
            }
            const emojiBtn = event.target?.closest?.("[data-emoji]");
            if (!emojiBtn) return;
            insertTextAtSelection(textarea, emojiBtn.getAttribute("data-emoji") || "", lastSelection);
            rememberSelection();
            closeEmojiPicker();
          });
          return;
        }
        if (action === "bold") return applyTagToSelection(textarea, "<b>", "</b>");
        if (action === "italic") return applyTagToSelection(textarea, "<i>", "</i>");
        if (action === "underline") return applyTagToSelection(textarea, "<u>", "</u>");
        if (action === "strike") return applyTagToSelection(textarea, "<s>", "</s>");
        if (action === "spoiler") return applyTagToSelection(textarea, "<tg-spoiler>", "</tg-spoiler>");
        if (action === "code") return applyTagToSelection(textarea, "<code>", "</code>");
        if (action === "pre") return applyTagToSelection(textarea, "<pre>", "</pre>");
        if (action === "quote") return applyTagToSelection(textarea, "<blockquote>", "</blockquote>");
        if (action === "quote_expandable") return applyTagToSelection(textarea, "<blockquote expandable>", "</blockquote>");
        if (action === "link") {
          const start = textarea.selectionStart ?? 0, end = textarea.selectionEnd ?? 0;
          const selected = (textarea.value || "").slice(start, end);
          const safeHref = sanitizeLinkHref((await window.HelpdeskDialogs.prompt("Введите URL (http/https/tg/mailto):", "https://")) || "");
          if (!safeHref) return;
          textarea.setSelectionRange(start, end);
          return selected ? applyTagToSelection(textarea, `<a href="${safeHref}">`, "</a>") : applyTagToSelection(textarea, `<a href="${safeHref}">ссылка</a>`, "");
        }
        if (action === "clear") {
          const source = textarea.value || "";
          const atomicRange = expandSelectionToAtomicTags(
            source,
            textarea.__telegramAtomicTags || [],
            textarea.selectionStart ?? 0,
            textarea.selectionEnd ?? 0,
          );
          const start = atomicRange.start;
          const end = atomicRange.end;
          const selected = source.slice(start, end);
          if (selected) {
            const cleaned = stripAllowedTelegramTags(selected);
            textarea.value = source.slice(0, start) + cleaned + source.slice(end);
            textarea.focus();
            textarea.setSelectionRange(start, start + cleaned.length);
          } else {
            textarea.value = stripAllowedTelegramTags(source);
            textarea.focus();
            textarea.setSelectionRange(textarea.value.length, textarea.value.length);
          }
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
    });
    if (typeof options.onSelectionChange === "function") {
      textarea.addEventListener("click", options.onSelectionChange);
      textarea.addEventListener("keyup", options.onSelectionChange);
      textarea.addEventListener("select", options.onSelectionChange);
    }
  }

  function bindPreview({ textarea, preview, validation }) {
    const sync = () => {
      if (!textarea || !preview) return;
      preview.innerHTML = renderTelegramPreviewHtml(textarea.value || "");
      const error = validateTelegramHtml(textarea.value || "");
      if (validation) validation.textContent = error;
    };
    textarea?.addEventListener("input", sync);
    sync();
    return sync;
  }

  function settingsHtml(options = {}) {
    return `
      <div class="telegram-editor-flags">
        <label class="checkbox-label">
          <input type="checkbox" data-telegram-disable-link-preview ${options.disableLinkPreview ? "checked" : ""} />
          <span>Отключить предпросмотр ссылки</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" data-telegram-send-silently ${options.sendSilently ? "checked" : ""} />
          <span>Отправить без звука</span>
        </label>
      </div>
    `;
  }

  Editor.escapeHtml = escapeHtml;
  Editor.toolbarHtml = toolbarHtml;
  Editor.settingsHtml = settingsHtml;
  Editor.bindToolbar = bindToolbar;
  Editor.bindPreview = bindPreview;
  Editor.insertTextAtSelection = insertTextAtSelection;
  Editor.normalizedAtomicTags = normalizedAtomicTags;
  Editor.findAtomicTagRanges = findAtomicTagRanges;
  Editor.expandSelectionToAtomicTags = expandSelectionToAtomicTags;
  Editor.snapCaretToAtomicBoundary = snapCaretToAtomicBoundary;
  Editor.atomicKeyEdit = atomicKeyEdit;
  Editor.replaceTextRange = replaceTextRange;
  Editor.atomicReplaceText = atomicReplaceText;
  Editor.bindAtomicTokens = bindAtomicTokens;
  Editor.renderTelegramPreviewHtml = renderTelegramPreviewHtml;
  Editor.validateTelegramHtml = validateTelegramHtml;
  Editor.closeEmojiPicker = closeEmojiPicker;
  global.TelegramTextEditor = Editor;
})(window);
