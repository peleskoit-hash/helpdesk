(function () {
  const EMPTY = "\u2014";

  function calendarDate(value, appTimezone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: appTimezone, year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(value);
    const fields = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return [fields.year, fields.month, fields.day].join("-");
  }

  function formatDate(value, appTimezone) {
    if (!value) return EMPTY;
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? EMPTY
      : date.toLocaleString("ru-RU", { timeZone: appTimezone, dateStyle: "short", timeStyle: "medium" });
  }

  function initReviewsPage({ root }) {
    const el = {};
    ["EmployeeField", "DateFrom", "DateTo", "Stats", "Count", "Average", "Status",
      "TableWrap", "TableBody", "Pagination", "Prev", "Next", "PageSummary"].forEach((key) => {
      el[key] = root.querySelector("#review" + key);
    });
    let admin = false;
    let appTimezone;
    let employee = null;
    let page = 1;
    let pages = 1;
    let generation = 0;
    let controller = new AbortController();
    let destroyed = false;
    let textBlocks = [];
    const historyCache = new Map();
    const listeners = [];
    const resizeObserver = new ResizeObserver(measureText);

    function node(tag, className, text) {
      const result = document.createElement(tag);
      if (className) result.className = className;
      if (text !== undefined) result.textContent = text;
      return result;
    }

    function listen(target, event, handler) {
      target.addEventListener(event, handler);
      listeners.push(() => target.removeEventListener(event, handler));
    }

    function status(message) {
      el.Status.textContent = message;
      el.Status.hidden = !message;
    }

    function clearResults() {
      generation += 1;
      controller.abort();
      controller = new AbortController();
      historyCache.clear();
      resizeObserver.disconnect();
      textBlocks = [];
      el.TableBody.replaceChildren();
      el.Stats.hidden = true;
      el.TableWrap.hidden = true;
      el.Pagination.hidden = true;
      return generation;
    }

    async function api(url, signal) {
      const response = await fetch(url, {
        headers: { accept: "application/json" },
        credentials: "include",
        cache: "no-store",
        signal,
      });
      if (response.status === 401) {
        window.location.href = "/login";
      }
      if (!response.ok) throw new Error("Ошибка запроса");
      return response.json();
    }

    function resetDates() {
      const today = calendarDate(new Date(), appTimezone);
      el.DateFrom.value = today.slice(0, 7) + "-01";
      el.DateTo.value = today;
      for (const input of [el.DateFrom, el.DateTo]) {
        input.removeAttribute("min");
        input.max = today;
      }
    }

    function measureText() {
      for (const entry of textBlocks) {
        if (!entry.expanded) {
          entry.button.hidden = entry.text.scrollHeight <= entry.text.clientHeight + 1;
        }
      }
    }

    function appendText(container, value) {
      const text = node("div", "reviews-text reviews-text-clamped", value || EMPTY);
      const button = node("button", "reviews-text-action", "Показать полностью");
      button.type = "button";
      button.hidden = true;
      button.setAttribute("aria-expanded", "false");
      const entry = { text, button, expanded: false };
      button.addEventListener("click", () => {
        entry.expanded = !entry.expanded;
        text.classList.toggle("reviews-text-clamped", !entry.expanded);
        button.textContent = entry.expanded ? "Свернуть" : "Показать полностью";
        button.setAttribute("aria-expanded", String(entry.expanded));
        measureText();
      });
      container.append(text, button);
      textBlocks.push(entry);
      resizeObserver.observe(text);
    }

    function renderHistory(container, items) {
      container.replaceChildren();
      if (!items.length) {
        container.textContent = "Предыдущих версий нет";
        return;
      }
      for (const item of items) {
        const version = node("div", "reviews-history-version");
        const meta = node("div", "reviews-history-meta");
        meta.append(
          node("span", "", "Версия " + item.version_number),
          node("span", "", formatDate(item.rated_at, appTimezone)),
          node("span", "", item.rating + " ★"),
        );
        version.append(meta);
        appendText(version, item.review_text);
        container.append(version);
      }
    }

    function appendHistory(current, cell) {
      const button = node("button", "reviews-text-action", "История");
      button.type = "button";
      button.setAttribute("aria-expanded", "false");
      const historyRow = node("tr");
      historyRow.hidden = true;
      const content = node("td", "reviews-history-cell");
      content.colSpan = 5;
      historyRow.append(content);
      cell.append(button);
      el.TableBody.append(historyRow);
      const entry = { loaded: false, loading: false };
      historyCache.set(current.review_id, entry);
      const rowGeneration = generation;

      button.addEventListener("click", async () => {
        if (historyCache.get(current.review_id) !== entry) return;
        historyRow.hidden = !historyRow.hidden;
        button.setAttribute("aria-expanded", String(!historyRow.hidden));
        if (historyRow.hidden || entry.loaded || entry.loading) return;
        entry.loading = true;
        content.textContent = "Загрузка истории...";
        try {
          const payload = await api("/api/v1/reviews/" + encodeURIComponent(current.review_id) + "/history", controller.signal);
          if (destroyed || rowGeneration !== generation) return;
          renderHistory(content, payload.items);
          entry.loaded = true;
        } catch (error) {
          if (destroyed || rowGeneration !== generation || error.name === "AbortError") return;
          content.textContent = "Не удалось загрузить историю. Закройте и откройте её повторно.";
        } finally {
          entry.loading = false;
        }
      });
    }

    function render(payload) {
      page = payload.page;
      pages = payload.pages;
      el.Count.textContent = String(payload.total_count);
      el.Average.textContent = payload.total_count && payload.average_rating != null
        ? Number(payload.average_rating).toFixed(2).replace(".", ",") : EMPTY;
      el.Stats.hidden = false;
      status(payload.items.length ? "" : "За выбранный период отзывов нет");
      el.TableWrap.hidden = !payload.items.length;
      for (const item of payload.items) {
        const row = node("tr");
        const subject = node("td");
        const link = node("a", "", item.ticket_subject_snapshot);
        link.href = "/tickets/" + encodeURIComponent(item.ticket_id);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        subject.append(link);
        const textCell = node("td");
        appendText(textCell, item.review_text);
        row.append(
          node("td", "", formatDate(item.rated_at, appTimezone)),
          node("td", "", item.client_name_snapshot),
          subject,
          node("td", "", item.rating + " ★"),
          textCell,
        );
        el.TableBody.append(row);
        if (item.has_history) appendHistory(item, textCell);
      }
      el.Pagination.hidden = pages <= 1;
      el.Prev.disabled = page <= 1;
      el.Next.disabled = page >= pages;
      el.PageSummary.textContent = "Страница " + page + " из " + pages;
    }

    async function loadReviews() {
      const requestGeneration = clearResults();
      if (admin && !employee.value) {
        status("Выберите сотрудника");
        return;
      }
      if (!el.DateFrom.value || !el.DateTo.value ||
          !el.DateFrom.checkValidity() || !el.DateTo.checkValidity() ||
          el.DateFrom.value > el.DateTo.value) {
        status("Укажите корректный период: дата начала не должна быть позже даты окончания.");
        return;
      }
      status("Загрузка отзывов...");
      const query = new URLSearchParams({
        date_from: el.DateFrom.value,
        date_to: el.DateTo.value,
        page: String(page),
        page_size: "30",
      });
      if (admin) query.set("employee_id", employee.value);
      try {
        const payload = await api("/api/v1/reviews?" + query, controller.signal);
        if (destroyed || requestGeneration !== generation) return;
        let datesChanged = false;
        for (const input of [el.DateFrom, el.DateTo]) {
          if (payload.min_review_date) {
            input.min = payload.min_review_date;
            if (input.value < input.min && input.min <= input.max) {
              input.value = input.min;
              datesChanged = true;
            }
          } else {
            input.removeAttribute("min");
          }
        }
        if (datesChanged) {
          page = 1;
          await loadReviews();
          return;
        }
        render(payload);
      } catch (error) {
        if (destroyed || requestGeneration !== generation || error.name === "AbortError") return;
        status("Не удалось загрузить отзывы. Повторите попытку.");
      }
    }

    async function start() {
      try {
        const sidebar = await window.HelpdeskSidebar.init();
        if (destroyed) return;
        admin = sidebar.role === "admin";
        appTimezone = sidebar.appTimezone;
        resetDates();
        if (admin) {
          const label = node("label", "", "Сотрудник:");
          employee = node("select");
          employee.id = "reviewEmployee";
          employee.disabled = true;
          const placeholder = node("option", "", "Выберите сотрудника");
          placeholder.value = "";
          employee.append(placeholder);
          label.append(employee);
          el.EmployeeField.append(label);
          el.EmployeeField.hidden = false;
          status("Загрузка сотрудников...");
          const employees = await api("/api/v1/reviews/employees", controller.signal);
          if (destroyed) return;
          for (const item of employees) {
            const option = node("option", "", item.display_name + (item.is_active ? "" : " (неактивен)"));
            option.value = item.employee_id;
            employee.append(option);
          }
          employee.disabled = false;
          listen(employee, "change", () => {
            page = 1;
            resetDates();
            el.DateFrom.disabled = el.DateTo.disabled = !employee.value;
            loadReviews();
          });
          status("Выберите сотрудника");
        } else {
          el.DateFrom.disabled = el.DateTo.disabled = false;
          await loadReviews();
        }
      } catch (error) {
        if (destroyed || error.name === "AbortError") return;
        status("Не удалось загрузить страницу отзывов. Обновите страницу.");
      }
    }

    for (const input of [el.DateFrom, el.DateTo]) {
      listen(input, "change", () => {
        page = 1;
        if (el.DateFrom.value && el.DateTo.value && el.DateFrom.value > el.DateTo.value) {
          const other = input === el.DateFrom ? el.DateTo : el.DateFrom;
          other.value = input.value;
        }
        loadReviews();
      });
    }
    listen(el.Prev, "click", () => {
      if (page > 1) { page -= 1; loadReviews(); }
    });
    listen(el.Next, "click", () => {
      if (page < pages) { page += 1; loadReviews(); }
    });
    start();
    return () => {
      destroyed = true;
      clearResults();
      listeners.forEach((remove) => remove());
    };
  }

  window.HelpdeskAppShell.registerPage("reviews", initReviewsPage);
})();
