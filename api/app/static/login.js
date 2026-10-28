(() => {
  const body = document.body;
  const hotspots = [...document.querySelectorAll(".lamp-hotspot")];
  const fields = [...document.querySelectorAll(".matrix-field")];
  // Deliberately in memory: the login scene never reads/writes application preferences.
  function switchTheme() {
    const theme = body.dataset.theme === "dark" ? "light" : "dark";
    body.dataset.theme = theme;
    hotspots.forEach(button => {
      button.setAttribute("aria-pressed", String(theme === "light"));
      button.setAttribute("aria-label", `Сменить освещение: включить ${theme === "dark" ? "светлую" : "тёмную"} тему`);
    });
    fields.forEach(field => render(field, true));
  }
  hotspots.forEach(button => button.addEventListener("click", switchTheme));

  const glyphs = new Map();
  function glyph(character) {
    if (glyphs.has(character)) return glyphs.get(character);
    const tile = document.createElement("canvas");
    tile.width = 18; tile.height = 26;
    const context = tile.getContext("2d", { willReadFrequently: true });
    context.font = "bold 22px monospace";
    context.textAlign = "center"; context.textBaseline = "middle";
    context.fillText(character, 9, 13);
    const pixels = context.getImageData(0, 0, 18, 26).data, dots = [];
    for (let y = 0; y < 13; y++) for (let x = 0; x < 9; x++) {
      if (pixels[((y * 2 + 1) * 18 + x * 2 + 1) * 4 + 3] > 60) dots.push([x, y]);
    }
    glyphs.set(character, dots);
    return dots;
  }
  const snapshots = new WeakMap();
  function render(field, force = false) {
    const input = field.querySelector("input"), canvas = field.querySelector("canvas");
    const width = field.clientWidth, height = field.clientHeight;
    const focused = document.activeElement === input;
    const signature = [input.value, input.selectionStart, input.selectionEnd, input.selectionDirection, width, height, focused, body.dataset.theme, devicePixelRatio].join("\0");
    if (!force && snapshots.get(field) === signature) return;
    snapshots.set(field, signature);
    const ratio = devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const context = canvas.getContext("2d");
    if (!context) return; // Native inputs stay visible if canvas is unavailable.
    context.scale(ratio, ratio);
    const characters = Array.from(input.value), pad = 16;
    // Prefer fitting ordinary logins; bound shrinking so long values remain readable and scroll.
    const pitch = Math.max(1.3, Math.min(2.6, height / 23, (width - pad * 2) / ((Math.min(characters.length, 24) + 1) * 8.5)));
    const cell = pitch * 8.5, baseline = (height - pitch * 13) / 2;
    const start = Array.from(input.value.slice(0, input.selectionStart ?? 0)).length;
    const end = Array.from(input.value.slice(0, input.selectionEnd ?? 0)).length;
    const caret = input.selectionDirection === "backward" ? start : end;
    const capacity = Math.max(1, Math.floor((width - pad * 2) / cell));
    const offset = input.value && focused ? Math.max(0, caret - capacity + 1) : 0;
    const ink = getComputedStyle(body).getPropertyValue("--matrix-ink").trim();
    context.save(); context.beginPath(); context.rect(pad - 2, 0, width - pad * 2 + 4, height); context.clip();
    for (let i = offset; i < Math.min(characters.length, offset + capacity + 1); i++) {
      const x = pad + (i - offset) * cell;
      if (focused && i >= start && i < end) { context.fillStyle = "#ffe4a545"; context.fillRect(x, baseline - 3, cell, pitch * 13 + 6); }
      // Never draw or cache the real password glyphs, including while composing.
      const character = input.type === "password" && input.value ? "●" : characters[i];
      context.fillStyle = ink;
      context.shadowColor = ink; context.shadowBlur = 6;
      for (const [dx, dy] of glyph(character)) {
        context.beginPath(); context.arc(x + dx * pitch, baseline + dy * pitch, pitch * .43, 0, Math.PI * 2); context.fill();
      }
    }
    context.restore();
    field.style.setProperty("--matrix-caret", `${Math.min(width - pad, pad + (caret - offset) * cell)}px`);
    field.classList.add("matrix-ready");
  }
  const observer = new ResizeObserver(() => {
    fields.forEach(field => render(field, true));
    // Cropped lamps must not trap keyboard navigation outside the visible scene.
    hotspots.forEach(button => {
      const rect = button.getBoundingClientRect();
      button.tabIndex = rect.right >= 44 && rect.left <= innerWidth - 44 && rect.bottom >= 44 && rect.top < innerHeight ? 0 : -1;
    });
  });
  fields.forEach(field => {
    const input = field.querySelector("input");
    for (const event of ["input", "change", "focus", "blur", "select", "keyup", "click", "compositionupdate", "compositionend"]) input.addEventListener(event, () => render(field));
    observer.observe(field);
    render(field);
  });
  document.addEventListener("selectionchange", () => fields.forEach(field => render(field)));
  // Password managers can assign .value without input/change events. Poll only for changes.
  setInterval(() => { if (!document.hidden) fields.forEach(field => render(field)); }, 150);
  window.addEventListener("pageshow", () => fields.forEach(field => render(field, true)));

  const error = document.getElementById("loginError");
  if (error.textContent.trim()) {
    const password = document.getElementById("passwordInput");
    password.setAttribute("aria-invalid", "true"); password.setAttribute("aria-describedby", "loginError"); password.focus();
  }
  // Optional metallic sound can later subscribe to this local event without changing submit.
  document.querySelector(".login-submit").addEventListener("pointerdown", () => {
    document.querySelector(".login-submit").dispatchEvent(new CustomEvent("login-metal-press"));
  });
})();
