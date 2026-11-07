(function () {
  const PAGE_SIZE = 50;
  const LABELS = { offline: "Не в сети", online: "В сети", on_ticket: "На заявке", waiting: "В ожидании", lunch: "Обед" };
  const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  function init({ root }) {
    const el = Object.fromEntries(["Employee", "Period", "DateFrom", "DateTo", "Order", "Message", "TableBody", "Empty", "Prev", "Next", "Summary"].map(key => [key, root.querySelector("#statusLog" + key)]));
    const dates = window.HelpdeskStatisticsDates;
    let timezone = "UTC", page = 1, cursors = [null], total = null, hasMore = false;
    let visible = false, loaded = false, loading = false, destroyed = false, generation = 0, ready = null;
    let controller = null;
    const lifetime = new AbortController();
    const listeners = [];
    const listen = (node, event, handler) => {
      node.addEventListener(event, handler);
      listeners.push(() => node.removeEventListener(event, handler));
    };

    async function api(url, signal) {
      const response = await fetch(url, { headers: { accept: "application/json" }, credentials: "include", cache: "no-store", signal });
      if (response.status === 401) window.location.href = "/login";
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.error?.message || "Не удалось загрузить лог смен статусов.");
      return payload;
    }

    function pagination(count = el.TableBody.children.length) {
      el.Prev.disabled = loading || page <= 1;
      el.Next.disabled = loading || !hasMore;
      if (total == null) { el.Summary.textContent = loading ? "Загрузка..." : ""; return; }
      const first = count ? (page - 1) * PAGE_SIZE + 1 : 0;
      el.Summary.textContent = `Показано ${first}–${count ? first + count - 1 : 0} из ${total} · Страница ${page} из ${Math.max(1, Math.ceil(total / PAGE_SIZE))}`;
    }

    function rowHtml(item) {
      const value = String(item.changed_at || "");
      const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + "Z");
      let day = "—", time = "—";
      if (!Number.isNaN(date.getTime())) {
        const f = Object.fromEntries(new Intl.DateTimeFormat("ru-RU", { timeZone: timezone, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(date).map(p => [p.type, p.value]));
        day = `${f.day}.${f.month}.${f.year}`;
        time = `${f.hour}:${f.minute}:${f.second}`;
      }
      const name = item.full_name == null ? "Удалённый сотрудник" : item.full_name;
      const meta = item.full_name == null ? item.user_id : item.is_active === false ? "Неактивен" : "";
      return `<tr><td data-label="Сотрудник"><div class="management-statistics-user-name">${escapeHtml(name)}</div>${meta ? `<div class="management-statistics-user-muted">${escapeHtml(meta)}</div>` : ""}</td><td data-label="Был статус">${escapeHtml(LABELS[item.from_status] || item.from_status || "—")}</td><td data-label="Стал статус">${escapeHtml(LABELS[item.to_status] || item.to_status || "—")}</td><td data-label="Дата">${escapeHtml(day)}</td><td data-label="Время">${escapeHtml(time)}</td><td data-label="Изменено Админом">${item.changed_by_admin === true ? "Да" : item.changed_by_admin === false ? "Автом." : ""}</td></tr>`;
    }

    async function loadPage() {
      if (destroyed || !visible) return;
      const token = ++generation;
      controller?.abort();
      controller = new AbortController();
      const bounds = dates.periodBounds(el.Period.value, el.DateFrom.value, el.DateTo.value, new Date(), timezone);
      el.TableBody.innerHTML = "";
      el.Empty.hidden = true;
      hasMore = false;
      if (bounds.error) {
        loading = false;
        el.Message.textContent = bounds.error;
        pagination(0);
        return;
      }
      loading = true;
      loaded = false;
      el.Message.textContent = "Загрузка...";
      pagination(0);
      const query = new URLSearchParams({ ...bounds.params, limit: String(PAGE_SIZE), sort_order: el.Order.value });
      if (el.Employee.value) query.set("user_id", el.Employee.value);
      if (cursors[page - 1]) query.set("cursor", cursors[page - 1]);
      if (total == null) query.set("include_total", "true");
      try {
        const payload = await api("/api/v1/management/statistics/status-transitions?" + query, controller.signal);
        if (destroyed || !visible || token !== generation) return;
        const items = payload.items || [];
        el.TableBody.innerHTML = items.map(rowHtml).join("");
        el.Empty.hidden = items.length > 0;
        hasMore = Boolean(payload.has_more && payload.next_cursor);
        if (hasMore) cursors[page] = payload.next_cursor;
        if (payload.total_count != null) total = Number(payload.total_count);
        loaded = true;
        el.Message.textContent = "";
      } catch (error) {
        if (destroyed || token !== generation || error.name === "AbortError") return;
        el.Message.textContent = error.message;
      } finally {
        if (!destroyed && token === generation) { loading = false; pagination(); }
      }
    }

    function refresh() {
      page = 1; cursors = [null]; total = null; loaded = false;
      root.querySelectorAll(".status-log-custom").forEach(node => { node.hidden = el.Period.value !== "custom"; });
      root.querySelectorAll("[data-status-log-sort]").forEach(node => node.setAttribute("aria-sort", el.Order.value === "asc" ? "ascending" : "descending"));
      loadPage();
    }

    function initialize() {
      if (!ready) ready = (async () => {
        const sidebar = await window.HelpdeskSidebar.init();
        if (sidebar.role !== "admin") throw new Error("Доступ только для администратора");
        timezone = sidebar.appTimezone || "UTC";
        const users = await api("/api/v1/settings/users", lifetime.signal);
        if (destroyed) return;
        el.Employee.innerHTML = '<option value="">Все сотрудники</option>' + users.filter(user => ["agent", "trainee"].includes(user.role)).map(user => `<option value="${escapeHtml(user.id)}">${escapeHtml((user.full_name || user.login || user.id) + (user.is_active === false ? " (неактивен)" : ""))}</option>`).join("");
        el.DateTo.value = dates.calendarDate(new Date(), timezone);
        el.DateFrom.value = dates.addCalendarDays(el.DateTo.value, -29);
        for (const control of [el.Employee, el.Period, el.DateFrom, el.DateTo, el.Order]) listen(control, "change", refresh);
        root.querySelectorAll(".status-log-sort").forEach(button => listen(button, "click", () => { el.Order.value = el.Order.value === "desc" ? "asc" : "desc"; refresh(); }));
        listen(el.Prev, "click", () => { if (!loading && page > 1) { page -= 1; loadPage(); } });
        listen(el.Next, "click", () => { if (!loading && hasMore) { page += 1; loadPage(); } });
      })().catch(error => { ready = null; throw error; });
      return ready;
    }

    return {
      async show() {
        visible = true;
        try { await initialize(); if (visible && !destroyed && !loaded && !loading) loadPage(); }
        catch (error) { if (!destroyed && error.name !== "AbortError") el.Message.textContent = error.message; }
      },
      hide() { visible = false; generation += 1; controller?.abort(); loading = false; },
      destroy() { destroyed = true; generation += 1; controller?.abort(); lifetime.abort(); listeners.forEach(remove => remove()); },
    };
  }
  window.HelpdeskStatusTransitions = { init };
})();
