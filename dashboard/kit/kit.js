/* Kit delle app di Ettore — versione 1.1 (2026-11-25).
   Funzioni comuni, senza dipendenze, per le regole tenute nell'app Standard: tema (UI-01), pannello sovrapposto
   (UI-07), menu del tasto destro (UX-10), scorciatoie (UX-11), rilettura dei dati (UX-14), conferma in due passi
   (UX-06), salvataggio automatico con stato (UX-04), link profondi (UX-09), avvisi.
   Si usa con kit.css; tutto sta in window.Kit. Novità e versioni: CHANGELOG.md accanto a questo file. */
(function () {
  "use strict";
  const Kit = { versione: "1.1" };
  const typing = target => target?.closest?.("input, textarea, select, [contenteditable='true']");

  /* ---------- Tema chiaro e scuro (UI-01) ----------
     Nell'<head>, prima dei fogli di stile, lo snippet anti-lampo del README imposta data-theme.
     Kit.theme.init("nomeapp") collega i pulsanti con l'attributo data-k-theme. */
  Kit.theme = {
    key: "app:theme",
    init(app) {
      this.key = `${app}:theme`;
      this.apply(document.documentElement.dataset.theme || this.saved() || this.system());
      document.querySelectorAll("[data-k-theme]").forEach(button => button.addEventListener("click", () => this.toggle()));
    },
    saved() { try { return localStorage.getItem(this.key); } catch { return null; } },
    system() { return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; },
    apply(theme) {
      document.documentElement.dataset.theme = theme;
      document.querySelectorAll("[data-k-theme]").forEach(button => {
        button.textContent = theme === "dark" ? "☾" : "☀";
        button.title = theme === "dark" ? "Tema scuro: passa al chiaro" : "Tema chiaro: passa allo scuro";
      });
    },
    toggle() {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(this.key, next); } catch { /* la scelta vale solo per questa visita */ }
      this.apply(next);
    },
  };

  /* ---------- Pannello laterale sovrapposto, con velo (UI-07) ----------
     Kit.panel(pannello, { toggle: pulsante, key: "nomeapp:panel" }) → { open, close, toggle }.
     Il pulsante sta dal lato in cui si apre il pannello (classe k-right per il lato destro). */
  Kit.panel = function (panel, { toggle = null, key = null } = {}) {
    const scrim = document.createElement("div");
    scrim.className = "k-scrim";
    scrim.hidden = true;
    document.body.append(scrim);
    const set = open => {
      panel.classList.toggle("k-open", open);
      scrim.hidden = !open;
      toggle?.setAttribute("aria-expanded", String(open));
      if (key) try { localStorage.setItem(key, open ? "open" : "closed"); } catch { /* non ricordato */ }
      if (open) panel.querySelector("button, a, input, [tabindex]")?.focus();
    };
    const api = { open: () => set(true), close: () => set(false), toggle: () => set(!panel.classList.contains("k-open")) };
    toggle?.addEventListener("click", api.toggle);
    scrim.addEventListener("click", api.close);
    document.addEventListener("keydown", event => { if (event.key === "Escape" && panel.classList.contains("k-open")) api.close(); });
    let saved = null;
    if (key) try { saved = localStorage.getItem(key); } catch { /* nessuna scelta */ }
    set(saved === "open");
    return api;
  };

  /* ---------- Menu del tasto destro e del tocco lungo (UX-10) ----------
     Kit.menu(contenitore, ".riga", elemento => [{ label, action }, "-", ...]). */
  let menuElement = null;
  const closeMenu = () => { if (menuElement) menuElement.hidden = true; };
  Kit.menu = function (container, selector, items) {
    if (!menuElement) {
      menuElement = document.createElement("div");
      menuElement.className = "k-menu";
      menuElement.setAttribute("role", "menu");
      menuElement.hidden = true;
      document.body.append(menuElement);
      document.addEventListener("click", event => { if (!menuElement.contains(event.target)) closeMenu(); });
      document.addEventListener("keydown", event => { if (event.key === "Escape") closeMenu(); });
      window.addEventListener("scroll", closeMenu, { passive: true });
    }
    const open = (target, x, y) => {
      menuElement.replaceChildren(...items(target).map(item => {
        if (item === "-") return document.createElement("hr");
        const button = document.createElement("button");
        button.textContent = item.label;
        button.setAttribute("role", "menuitem");
        button.addEventListener("click", () => { closeMenu(); item.action(); });
        return button;
      }));
      menuElement.hidden = false;
      const box = menuElement.getBoundingClientRect();
      menuElement.style.left = `${Math.max(8, Math.min(x, innerWidth - box.width - 8))}px`;
      menuElement.style.top = `${Math.max(8, Math.min(y, innerHeight - box.height - 8))}px`;
      menuElement.querySelector("button")?.focus();
    };
    container.addEventListener("contextmenu", event => {
      const target = event.target.closest(selector);
      if (!target || typing(event.target)) return;
      event.preventDefault();
      open(target, event.clientX, event.clientY);
    });
    let timer = null;
    container.addEventListener("pointerdown", event => {
      const target = event.target.closest(selector);
      if (!target || event.pointerType === "mouse" || typing(event.target)) return;
      timer = setTimeout(() => open(target, event.clientX, event.clientY), 550);
    });
    ["pointerup", "pointermove", "pointercancel"].forEach(name => container.addEventListener(name, () => clearTimeout(timer)));
  };

  /* ---------- Scorciatoie (UX-11) ----------
     Kit.keys({ "/": fn, "j": fn, "ctrl+k": fn, "Escape": fn }). Mentre si scrive in un campo vale solo Escape. */
  Kit.keys = function (map) {
    document.addEventListener("keydown", event => {
      // Invio e spazio su pulsanti e link restano loro: premono il pulsante, aprono il link (1.1).
      if ((event.key === "Enter" || event.key === " ") &&
          event.target.closest?.("button, a[href], summary, [role='button'], [role='menuitem'], [role='tab']")) return;
      const name = `${event.ctrlKey || event.metaKey ? "ctrl+" : ""}${event.key.length === 1 ? event.key.toLowerCase() : event.key}`;
      const action = map[name];
      if (!action || (typing(event.target) && event.key !== "Escape") || (event.altKey && !name.startsWith("ctrl+"))) return;
      event.preventDefault();
      action(event);
    });
  };

  /* ---------- Rilettura dei dati (UX-14) ----------
     Kit.refresh(load, { every: 60000, skipIf: () => modificheInCorso }): rilegge quando la scheda torna visibile
     e a intervalli solo mentre è visibile. skipIf protegge le modifiche non ancora salvate. */
  Kit.refresh = function (load, { every = 60_000, skipIf = () => false } = {}) {
    const run = () => { if (!document.hidden && !skipIf()) load(); };
    document.addEventListener("visibilitychange", run);
    if (every) setInterval(run, every);
  };

  /* ---------- Conferma in due passi, vicino all'elemento (UX-06) ----------
     Kit.confirm(pulsante, azione, { label: "Conferma", ms: 3000 }). */
  Kit.confirm = function (button, action, { label = "Conferma", ms = 3000 } = {}) {
    let timer = null;
    const original = button.textContent;
    const reset = () => { clearTimeout(timer); timer = null; button.textContent = original; button.classList.remove("k-confirming"); };
    button.addEventListener("click", () => {
      if (timer) { reset(); action(); return; }
      button.textContent = label;
      button.classList.add("k-confirming");
      timer = setTimeout(reset, ms);
    });
  };

  /* ---------- Salvataggio automatico con stato (UX-04) ----------
     const save = Kit.autosave(async () => { ...PUT... }, { status: elemento, delay: 800 }); save() a ogni modifica.
     I salvataggi vanno in fila: una modifica arrivata mentre si salva parte subito dopo, mai in parallelo (1.1).
     Durante l'attesa lo stato dice "Modifiche da salvare…" (1.1). Se la scrittura lancia un errore con
     error.stop = true (per esempio un 409: dati cambiati altrove), non si ritenta finché l'utente non modifica di nuovo.
     I testi finali iniziano sempre con "Salvato" o "Non salvato": alcune app li controllano prima di chiudere la pagina. */
  Kit.autosave = function (write, { status = null, delay = 800 } = {}) {
    let timer = null, pending = false, running = null;
    const show = (text, kind = "") => {
      if (!status) return;
      status.textContent = text;
      status.classList.toggle("k-error", kind === "error");
      status.classList.toggle("k-pending", kind === "pending");
    };
    const flush = async () => {
      clearTimeout(timer);
      if (running) return running;   // il salvataggio in corso, finendo, riparte se nel frattempo è cambiato qualcosa
      if (!pending) return;
      running = (async () => {
        while (pending) {
          pending = false;
          show("Salvataggio…", "pending");
          try {
            await write();
            if (!pending) show(`Salvato alle ${new Date().toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}`);
          } catch (error) {
            pending = !error?.stop;
            show(`Non salvato${error?.message ? ` (${error.message})` : ""}: le modifiche sono ancora qui.`, "error");
            break;
          }
        }
      })();
      try { await running; } finally { running = null; }
    };
    const schedule = () => {
      pending = true;
      clearTimeout(timer);
      if (!running) show("Modifiche da salvare…", "pending");
      timer = setTimeout(flush, delay);
    };
    schedule.flush = flush;
    schedule.pending = () => pending || Boolean(running);
    document.addEventListener("visibilitychange", () => { if (document.hidden) flush(); });
    return schedule;
  };

  /* ---------- Link profondi (UX-09) ----------
     Kit.route(parti => mostra(parti)) è chiamata all'avvio e a ogni cambio di #tab/id; Kit.go("tab", "id") lo cambia. */
  Kit.route = function (show) {
    const read = () => show(decodeURIComponent(location.hash.slice(1)).split("/").filter(Boolean));
    window.addEventListener("hashchange", read);
    read();
  };
  Kit.go = (...parts) => { location.hash = parts.map(encodeURIComponent).join("/"); };

  /* ---------- Avvisi brevi ---------- */
  let toastElement = null, toastTimer = null;
  Kit.toast = function (text, ms = 2500) {
    if (!toastElement) {
      toastElement = document.createElement("div");
      toastElement.className = "k-toast";
      toastElement.setAttribute("role", "status");
      document.body.append(toastElement);
    }
    toastElement.textContent = text;
    toastElement.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastElement.hidden = true; }, ms);
  };

  /* Copia negli appunti con avviso: utile nelle voci "Copia link" dei menu. */
  Kit.copy = async function (text, message = "Copiato") {
    try { await navigator.clipboard.writeText(text); Kit.toast(message); } catch { Kit.toast("Copia non riuscita"); }
  };

  window.Kit = Kit;
})();
