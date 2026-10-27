(function () {
  function fill(columns, available, systemWidth) {
    const occupied = columns.reduce((sum, column) => sum + (column.key === "subject" ? 0 : column.width), systemWidth);
    return columns.map((column) => column.key === "subject"
      ? { ...column, width: Math.max(180, Math.floor(available - occupied)) } : { ...column });
  }

  function measure(table, columns, samples) {
    const probe = table.cloneNode(true);
    probe.removeAttribute("id");
    probe.style.cssText = "position:absolute;left:0;top:0;visibility:hidden;pointer-events:none;width:max-content!important;min-width:0!important;table-layout:auto!important";
    probe.setAttribute("aria-hidden", "true");
    probe.tBodies[0].replaceChildren(...samples.map((row) => row.cloneNode(true)));
    probe.querySelectorAll(".column-resize-handle").forEach((node) => node.remove());
    probe.querySelectorAll("th, td").forEach((cell) => {
      cell.style.cssText = "width:auto!important;min-width:0!important;max-width:none!important;white-space:nowrap!important";
    });
    table.parentElement.appendChild(probe);
    try {
      const pixels = (node, property) => parseFloat(getComputedStyle(node)[property]) || 0;
      const padding = (node) => pixels(node, "paddingLeft") + pixels(node, "paddingRight")
        + pixels(node, "borderLeftWidth") + pixels(node, "borderRightWidth");
      const contentWidth = (cell, dataOnly = false) => {
        const content = cell.querySelector(".table-sort-btn, .ticket-row-link, select") || cell;
        const select = content.tagName === "SELECT";
        const text = (select ? content.selectedOptions[0]?.textContent : content.textContent)?.trim() || "";
        if (!text || /^[—–-]$/.test(text)) return 0;
        if (select) {
          const span = document.createElement("span");
          const style = getComputedStyle(content);
          span.style.font = style.font;
          span.style.letterSpacing = style.letterSpacing;
          span.textContent = text;
          cell.appendChild(span);
          const width = span.getBoundingClientRect().width + (dataOnly ? 0 : padding(content) + 20 + padding(cell));
          span.remove();
          return width;
        }
        content.style.setProperty("width", "max-content", "important");
        content.style.setProperty("max-width", "none", "important");
        content.style.setProperty("display", "inline-block", "important");
        content.style.setProperty("white-space", "nowrap", "important");
        if (content === cell) {
          const range = document.createRange();
          range.selectNodeContents(cell);
          return range.getBoundingClientRect().width + (dataOnly ? 0 : padding(cell));
        }
        return content.getBoundingClientRect().width + (dataOnly ? -padding(content) : padding(cell));
      };
      return columns.map((column) => {
        if (column.key === "subject") return { key: column.key, width: 180 };
        const selector = `[data-column-key="${CSS.escape(column.key)}"]`;
        const header = probe.tHead.querySelector(selector);
        const minimum = Math.max(60, contentWidth(header));
        const values = [...probe.tBodies[0].querySelectorAll(selector)].map((cell) => {
          const width = contentWidth(cell, true);
          return { width, extra: contentWidth(cell) - width };
        }).filter((value) => value.width > 0);
        const target = values.length ? Math.min(Math.max(...values.map((value) => value.width)), values.reduce((sum, value) => sum + value.width, 0) / values.length * 1.5) + Math.max(...values.map((value) => value.extra)) : 0;
        return { key: column.key, width: Math.ceil(Math.max(minimum, target)) };
      });
    } finally {
      probe.remove();
    }
  }
  window.HelpdeskTicketColumnAutoWidth = { fill, measure };
})();
