(function () {
  function fill(columns, available, systemWidth) {
    const occupied = columns.reduce((sum, column) => sum + (column.key === "subject" ? 0 : column.width), systemWidth);
    return columns.map((column) => column.key === "subject"
      ? { ...column, width: Math.max(180, Math.floor(available - occupied)) } : { ...column });
  }

  function measure(table, columns, samples) {
    const probe = table.cloneNode(false);
    probe.append(table.tHead.cloneNode(true), table.tBodies[0].cloneNode(false));
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
      // Prepare every cell before measuring: interleaved writes/reads relayout the whole table.
      probe.querySelectorAll(".table-sort-btn, .ticket-row-link").forEach((content) => {
        content.style.setProperty("width", "max-content", "important");
        content.style.setProperty("max-width", "none", "important");
        content.style.setProperty("display", "inline-block", "important");
        content.style.setProperty("white-space", "nowrap", "important");
      });
      const selectSamples = new Map([...probe.querySelectorAll("select")].map((select) => {
        const span = document.createElement("span");
        const style = getComputedStyle(select);
        span.style.font = style.font;
        span.style.letterSpacing = style.letterSpacing;
        span.textContent = select.selectedOptions[0]?.textContent?.trim() || "";
        // Measure the browser's native arrow, border and padding for the selected name only.
        const option = select.selectedOptions[0]?.cloneNode(true);
        select.replaceChildren(...(option ? [option] : []));
        select.style.setProperty("width", "auto", "important");
        select.style.setProperty("min-width", "0", "important");
        select.style.setProperty("max-width", "none", "important");
        return [select, span];
      }));
      selectSamples.forEach((span, select) => select.parentElement.appendChild(span));
      const pixels = (node, property) => parseFloat(getComputedStyle(node)[property]) || 0;
      const padding = (node) => pixels(node, "paddingLeft") + pixels(node, "paddingRight")
        + pixels(node, "borderLeftWidth") + pixels(node, "borderRightWidth");
      const contentWidth = (cell, dataOnly = false) => {
        const content = cell.querySelector(".table-sort-btn, .ticket-row-link, select") || cell;
        const select = content.tagName === "SELECT";
        const text = (select ? content.selectedOptions[0]?.textContent : content.textContent)?.trim() || "";
        if (!text || /^[—–-]$/.test(text)) return 0;
        if (select) {
          return dataOnly ? selectSamples.get(content).getBoundingClientRect().width
            : content.getBoundingClientRect().width + padding(cell);
        }
        if (content === cell) {
          const range = document.createRange();
          range.selectNodeContents(cell);
          return range.getBoundingClientRect().width + (dataOnly ? 0 : padding(cell));
        }
        return content.getBoundingClientRect().width + (dataOnly ? -padding(content) : padding(cell));
      };
      const sortButtons = [...probe.tHead.querySelectorAll(".table-sort-btn[data-sort-key]")];
      sortButtons.forEach((button) => {
        button.textContent = `${button.textContent.replace(/\s*[▲▼]\s*$/, "").trim()} ▲`;
      });
      const headerWidths = new Map(sortButtons.map((button) => {
        const header = button.closest("th");
        return [header, contentWidth(header)];
      }));
      sortButtons.forEach((button) => {
        button.textContent = button.textContent.replace(/▲$/, "▼");
      });
      const measured = columns.map((column) => {
        if (column.key === "subject") return { key: column.key, width: 180 };
        const selector = `[data-column-key="${CSS.escape(column.key)}"]`;
        const header = probe.tHead.querySelector(selector);
        const minimum = Math.max(60, headerWidths.get(header) || 0, contentWidth(header));
        const values = [...probe.tBodies[0].querySelectorAll(selector)].map((cell) => {
          const width = contentWidth(cell, true);
          return { width, extra: contentWidth(cell) - width };
        }).filter((value) => value.width > 0);
        const target = values.length ? Math.min(Math.max(...values.map((value) => value.width)), values.reduce((sum, value) => sum + value.width, 0) / values.length * 2) + Math.max(...values.map((value) => value.extra)) : 0;
        return { key: column.key, width: Math.ceil(Math.max(minimum, target)), fullWidth: Math.ceil(Math.max(minimum, ...values.map(value => value.width + value.extra))) };
      });
      // The 2× cap is the baseline. Use only spare subject space for uncapped content,
      // fitting small deficits first so an outlier cannot crowd out ordinary names.
      const scroll = table.closest(".table-scroll");
      const style = getComputedStyle(scroll);
      const systemWidth = [...table.tHead.rows[0].cells].filter(cell => !cell.dataset.columnKey)
        .reduce((sum, cell) => sum + cell.getBoundingClientRect().width, 0);
      let spare = Math.max(0, Math.floor(scroll.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
        - systemWidth - measured.reduce((sum, column) => sum + column.width, 0)));
      measured.filter(column => column.fullWidth > column.width)
        .sort((a, b) => (a.fullWidth - a.width) - (b.fullWidth - b.width)).forEach(column => {
          const extra = Math.min(spare, column.fullWidth - column.width);
          column.width += extra;
          spare -= extra;
        });
      return measured.map(({ key, width }) => ({ key, width }));
    } finally {
      probe.remove();
    }
  }
  window.HelpdeskTicketColumnAutoWidth = { fill, measure };
})();
