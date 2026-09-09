(function () {
  const EMPTY = "\u2014";
  const PAGE_SIZE = 30;
  const TEXT_PREVIEW_LIMIT = 200;

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

  function objectTypeLabel(workflowType) {
    return workflowType === "task" ? "Задача" : "Заявка";
  }

  function objectNumber(item) {
    return item.ticket_number || "";
  }

  function initManagementFeedbackPage({ root }) {
    const el = {};
    ["Status", "Search", "DateFrom", "DateTo", "SearchBtn", "ResetBtn", "Summary",
      "TableBody", "Empty", "Pagination", "Prev", "Next", "PageSummary"].forEach((key) => {
      el[key] = root.querySelector("#managementFeedback" + key);
    });
    let appTimezone = "UTC";
    let page = 1;
    let pages = 1;
    let generation = 0;
    let controller = new AbortController();
    let destroyed = false;
    const listeners = [];

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

    function setStatus(message) {
      el.Status.textContent = message;
      el.Status.hidden = !message;
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

    function clearResults() {
      generation += 1;
      controller.abort();
      controller = new AbortController();
      el.TableBody.replaceChildren();
      el.Empty.hidden = false;
      el.Empty.textContent = "Загрузка...";
      el.Summary.hidden = true;
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

    function appendText(container, value) {
      const fullText = String(value || EMPTY);
      const shouldCollapse = fullText.length > TEXT_PREVIEW_LIMIT;
      const collapsedText = shouldCollapse ? fullText.slice(0, TEXT_PREVIEW_LIMIT) + "\u2026" : fullText;
      const text = node("div", "management-feedback-text", collapsedText);
      if (!shouldCollapse) {
        container.append(text);
        return;
      }
      const button = node("button", "management-feedback-text-action", "Показать полностью");
      button.type = "button";
      button.setAttribute("aria-expanded", "false");
      let expanded = false;
      button.addEventListener("click", () => {
        expanded = !expanded;
        text.textContent = expanded ? fullText : collapsedText;
        button.textContent = expanded ? "Свернуть" : "Показать полностью";
        button.setAttribute("aria-expanded", String(expanded));
      });
      container.append(text, button);
    }

    function appendObject(container, item) {
      if (!item.ticket_available) {
        container.append(
          node("div", "management-feedback-object-type", "Объект недоступен"),
          node("div", "management-feedback-object-title", item.ticket_title_snapshot || EMPTY),
        );
        return;
      }
      const type = objectTypeLabel(item.workflow_type);
      const number = objectNumber(item);
      const title = node("div", "management-feedback-object-title", item.ticket_title_snapshot || EMPTY);
      const routeId = number || item.ticket_id || "";
      const link = node("a", "", number ? "№" + number : "Открыть объект");
      link.href = "/tickets/" + encodeURIComponent(routeId);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const line = node("div", "");
      line.append(node("span", "management-feedback-object-type", "[" + type + "] "), link);
      container.append(line, title);
    }

    function render(payload) {
      page = payload.page;
      pages = payload.pages;
      el.Empty.hidden = Boolean(payload.items.length);
      el.Empty.textContent = "Предложений и замечаний за выбранный период нет";
      el.Summary.textContent = "Найдено: " + payload.total_count;
      el.Summary.hidden = false;
      for (const item of payload.items) {
        const row = node("tr");
        const objectCell = node("td");
        const textCell = node("td");
        appendObject(objectCell, item);
        appendText(textCell, item.text);
        row.append(
          node("td", "", formatDate(item.submitted_at, appTimezone)),
          objectCell,
          node("td", "", item.client_name_snapshot || EMPTY),
          textCell,
        );
        el.TableBody.append(row);
      }
      el.Pagination.hidden = pages <= 1;
      el.Prev.disabled = page <= 1;
      el.Next.disabled = page >= pages;
      el.PageSummary.textContent = "Страница " + page + " из " + pages;
      setStatus("");
    }

    async function loadFeedback() {
      const requestGeneration = clearResults();
      if (!el.DateFrom.value || !el.DateTo.value ||
          !el.DateFrom.checkValidity() || !el.DateTo.checkValidity() ||
          el.DateFrom.value > el.DateTo.value) {
        el.Empty.textContent = "";
        setStatus("Укажите корректный период: дата начала не должна быть позже даты окончания.");
        return;
      }
      setStatus("Загрузка...");
      const query = new URLSearchParams({
        date_from: el.DateFrom.value,
        date_to: el.DateTo.value,
        page: String(page),
        page_size: String(PAGE_SIZE),
      });
      const search = (el.Search.value || "").trim();
      if (search) query.set("query", search);
      try {
        const payload = await api("/api/v1/management/feedback?" + query, controller.signal);
        if (destroyed || requestGeneration !== generation) return;
        let datesChanged = false;
        for (const input of [el.DateFrom, el.DateTo]) {
          if (payload.min_feedback_date) {
            input.min = payload.min_feedback_date;
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
          await loadFeedback();
          return;
        }
        render(payload);
      } catch (error) {
        if (destroyed || requestGeneration !== generation || error.name === "AbortError") return;
        el.Empty.textContent = "";
        setStatus("Не удалось загрузить предложения и замечания. Повторите попытку.");
      }
    }

    function runSearch() {
      page = 1;
      loadFeedback();
    }

    async function start() {
      try {
        const sidebar = await window.HelpdeskSidebar.init();
        if (destroyed) return;
        if (sidebar.role !== "admin") {
          setStatus("Доступ только для администратора");
          return;
        }
        appTimezone = sidebar.appTimezone || "UTC";
        resetDates();
        await loadFeedback();
      } catch (error) {
        if (destroyed || error.name === "AbortError") return;
        setStatus("Не удалось загрузить страницу. Обновите страницу.");
      }
    }

    for (const input of [el.DateFrom, el.DateTo]) {
      listen(input, "change", () => {
        page = 1;
        if (el.DateFrom.value && el.DateTo.value && el.DateFrom.value > el.DateTo.value) {
          const other = input === el.DateFrom ? el.DateTo : el.DateFrom;
          other.value = input.value;
        }
        loadFeedback();
      });
    }
    listen(el.SearchBtn, "click", runSearch);
    listen(el.Search, "keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      runSearch();
    });
    listen(el.ResetBtn, "click", () => {
      el.Search.value = "";
      page = 1;
      resetDates();
      loadFeedback();
    });
    listen(el.Prev, "click", () => {
      if (page > 1) { page -= 1; loadFeedback(); }
    });
    listen(el.Next, "click", () => {
      if (page < pages) { page += 1; loadFeedback(); }
    });

    start();
    return () => {
      destroyed = true;
      clearResults();
      listeners.forEach((remove) => remove());
    };
  }

  window.HelpdeskAppShell.registerPage("management-feedback", initManagementFeedbackPage);
})();
