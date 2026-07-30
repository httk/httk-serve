(() => {
  "use strict";

  const setButton = (button, token) => {
    button.dataset.token = token || "";
    button.disabled = !token;
  };

  const install = (table) => {
    if (table.dataset.httkServeTableReady === "1") return;
    table.dataset.httkServeTableReady = "1";
    const previous = table.querySelector("[data-httk-serve-table-previous]");
    const next = table.querySelector("[data-httk-serve-table-next]");
    const status = table.querySelector("[data-httk-serve-table-status]");
    const tbody = table.querySelector("tbody");
    if (!previous || !next || !status || !tbody) return;

    const requestPage = async (button) => {
      const token = button.dataset.token;
      if (!token || table.getAttribute("aria-busy") === "true") return;
      table.setAttribute("aria-busy", "true");
      previous.disabled = true;
      next.disabled = true;
      status.textContent = "Loading page…";
      try {
        const response = await fetch(table.dataset.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            token,
            route: table.dataset.route,
            widget_id: table.dataset.widgetId,
          }),
        });
        if (response.status === 409 || response.status === 410) {
          setButton(previous, "");
          setButton(next, "");
          status.textContent = response.status === 410
            ? "Pagination expired. Reload the page to continue."
            : "Table data changed. Reload the page to continue.";
          return;
        }
        if (!response.ok) throw new Error("table request failed");
        const payload = await response.json();
        if (typeof payload.tbody !== "string") throw new Error("invalid table response");
        tbody.innerHTML = payload.tbody;
        setButton(previous, payload.previous);
        setButton(next, payload.next);
        status.textContent = typeof payload.summary === "string" ? payload.summary : "Page loaded.";
        table.dispatchEvent(new CustomEvent("httk-serve:table-updated", {
          bubbles: true,
          detail: { table, total: payload.total },
        }));
      } catch (_error) {
        status.textContent = "Could not load this page. Please try again.";
        setButton(previous, previous.dataset.token);
        setButton(next, next.dataset.token);
      } finally {
        table.setAttribute("aria-busy", "false");
      }
    };

    previous.addEventListener("click", () => requestPage(previous));
    next.addEventListener("click", () => requestPage(next));
  };

  const start = () => document.querySelectorAll("[data-httk-serve-table]").forEach(install);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
