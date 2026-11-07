(() => {
  "use strict";
  // One in-memory snapshot, scoped to the authenticated sidebar identity and session.
  let scope = "", generation = 0, cached = null;
  window.HelpdeskTaskPanelLayout = {
    restore(state, identity) {
      const next = identity?.userId ? JSON.stringify([String(identity.userId), identity.role, Number(identity.sessionLoginTs || 0)]) : "";
      if (next !== scope) { scope = next; generation++; cached = null; }
      if (state.taskPanelScope === generation) return;
      state.taskPanelScope = generation;
      state.taskPanelEnabled = cached?.enabled === true;
      state.taskPanelItems = cached?.items || [];
      state.taskPanelStatus = cached?.status || "loading";
    },
    isCurrent(token) { return token === generation; },
    remember(state) {
      if (!scope || state.taskPanelScope !== generation) return;
      const enabled = (state.role === "agent" || state.role === "trainee") && state.taskPanelEnabled;
      cached = { enabled, items: enabled ? state.taskPanelItems : [], status: state.taskPanelStatus };
    },
    mount(panel, render) {
      const toolbar = panel.closest(".toolbar");
      const search = toolbar.querySelector("#searchInput");
      const anchor = toolbar.querySelector(".search-input-wrap, #backToListBtn");
      const settings = toolbar.querySelector(".display-settings");
      const maximum = toolbar.closest("[data-shell-page]").dataset.shellPage === "tickets-list" ? 6 : 7;
      const canvas = document.createElement("canvas");
      let frame = 0, destroyed = false;
      function setStyle(node, property, value) {
        if (node.style[property] !== value) node.style[property] = value;
      }
      function fitSearch() {
        if (!search) return;
        const style = getComputedStyle(search), ctx = canvas.getContext("2d");
        ctx.font = style.font;
        const width = Math.ceil(ctx.measureText(search.placeholder).width + 16 + 8 + 24
          + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth));
        search.parentElement.style.setProperty("--compact-search-width", `${width}px`);
      }
      function schedule() {
        if (destroyed || frame) return;
        frame = requestAnimationFrame(() => { frame = 0; fitSearch(); render(); positionDropdown(); });
      }
      function positionDropdown() {
        const menu = panel.querySelector("#taskPanelDropdown");
        if (menu.classList.contains("hidden")) return;
        const button = panel.querySelector("#taskPanelMoreBtn").getBoundingClientRect();
        setStyle(menu, "left", `${Math.max(8, Math.min(button.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`);
        setStyle(menu, "top", `${button.bottom + 6}px`);
        setStyle(menu, "maxHeight", `${Math.max(0, Math.min(360, innerHeight - button.bottom - 14))}px`);
      }
      const observer = new ResizeObserver(schedule);
      [toolbar, anchor, settings].forEach(node => observer.observe(node));
      fitSearch();
      document.fonts.ready.then(schedule);
      window.addEventListener("resize", schedule);
      window.addEventListener("scroll", positionDropdown, true);
      return {
        capacity(count) {
          // left/top are CSS coordinates in the toolbar's padding box, not viewport coordinates.
          const a = { right: anchor.offsetLeft + anchor.offsetWidth, top: anchor.offsetTop, height: anchor.offsetHeight };
          const right = { left: settings.offsetLeft, top: settings.offsetTop, height: settings.offsetHeight };
          const style = getComputedStyle(toolbar);
          const gap = parseFloat(style.columnGap) || 8;
          const sameRow = Math.abs(a.top + a.height / 2 - right.top - right.height / 2) < 2;
          const end = sameRow ? right.left - gap
            : toolbar.clientWidth - parseFloat(style.paddingRight);
          const width = Math.max(0, end - a.right - gap);
          setStyle(panel, "left", `${a.right + gap}px`);
          setStyle(panel, "top", `${a.top + (a.height - 30) / 2}px`);
          setStyle(panel, "width", `${width}px`);
          setStyle(panel, "visibility", width >= 44 ? "" : "hidden");
          const minimum = parseFloat(getComputedStyle(panel).getPropertyValue("--task-panel-chip-min")) || 104;
          if (count <= maximum && width >= count * minimum + Math.max(0, count - 1) * 6) return count;
          return Math.max(0, Math.min(maximum, Math.floor((width - 44) / (minimum + 6))));
        },
        positionDropdown,
        destroy() {
          destroyed = true;
          cancelAnimationFrame(frame);
          observer.disconnect();
          window.removeEventListener("resize", schedule);
          window.removeEventListener("scroll", positionDropdown, true);
        },
      };
    },
  };
})();
