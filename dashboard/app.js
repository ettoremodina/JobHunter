"use strict";
const $ = (id) => document.getElementById(id);
const labels = {
  new: "Nuova",
  review: "Da approfondire",
  saved: "Interessante",
  discarded: "Scartata",
  contacted: "Contattata",
};
let token = "",
  config = {},
  offset = 0,
  total = 0,
  selected = null,
  requestNumber = 0;
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
}
async function api(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-JobHunter-Token": token,
          },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Operazione non riuscita");
  return result;
}
function guarded(action) {
  return async (event) => {
    try {
      await action(event);
    } catch (error) {
      message(error.message, true);
    }
  };
}
function date(value) {
  return value
    ? new Date(value).toLocaleDateString("it-IT")
    : "Non disponibile";
}
function link(address, text) {
  try {
    const parsed = new URL(address);
    if (!["http:", "https:"].includes(parsed.protocol))
      return el("span", "Link non disponibile");
    const a = el("a", text);
    a.href = parsed.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  } catch {
    return el("span", "Link non disponibile");
  }
}
function params(start = offset, size = config.page_size) {
  return new URLSearchParams({
    query: $("query").value,
    status: $("status").value,
    source: $("source").value,
    location: $("location").value,
    category: $("category").value,
    offset: start,
    limit: size,
  });
}

/** Keep list rows short even when a source stores many places in one string. */
function locationPreview(locations) {
  const text = locations.join(" · ").replace(/^[,\s]+/, "") || "Da verificare";
  return text.length > 90 ? text.slice(0, 87).trimEnd() + "…" : text;
}

/** Reveal the source's full location text without guessing comma-separated cities. */
function locationDetails(locations) {
  const full = locations.join(" · ").replace(/^[,\s]+/, "");
  if (full.length <= 90) return el("p", full || "Località da verificare");
  const details = el("details", undefined, "locations");
  details.append(el("summary", locationPreview(locations) + " Mostra tutte"), el("p", full));
  return details;
}

/** Render common scraped Markdown as text nodes, paragraphs and lists, never HTML. */
function description(text) {
  const block = el("div", undefined, "description");
  const normalized = (text || "Descrizione non disponibile")
    .replace(/\r\n?/g, "\n")
    .replace(/\\([\\`*_{}\[\]()#+.!-])/g, "$1")
    .replace(/\*\*([^*\n]+)\*\*\s*-{3,}/g, "\n\n## $1\n\n")
    .replace(/([^\n])\s+\* (?!\*)/g, "$1\n* ")
    .replace(/\n[ \t]*[-_]{3,}[ \t]*(?=\n|$)/g, "\n")
    .trim();
  let list = null;
  for (const line of normalized.split(/\n+/)) {
    const value = line.trim();
    if (!value) continue;
    const bullet = value.match(/^(?:[-*•]|\d+\.)\s+(.+)/);
    const heading = value.match(/^#{1,6}\s+(.+)/) || value.match(/^\*\*(.+?)\*\*:?$/);
    const readable = (bullet ? bullet[1] : heading ? heading[1] : value).replace(/\*\*(.+?)\*\*/g, "$1");
    if (bullet) {
      if (!list) { list = el("ul"); block.append(list); }
      list.append(el("li", readable));
    } else {
      list = null;
      block.append(el(heading ? "h4" : "p", readable));
    }
  }
  return block;
}
async function load() {
  const request = ++requestNumber;
  $("count").textContent = "Caricamento…";
  const data = await api("/api/companies?" + params());
  if (request !== requestNumber) return;
  total = data.total;
  $("rows").replaceChildren();
  $("count").textContent = `${total.toLocaleString("it-IT")} aziende`;
  for (const company of data.items) {
    const tr = el("tr");
    if (company.id === selected) tr.className = "selected";
    const name = el("td");
    const button = el("button", company.name, "company-link");
    button.addEventListener(
      "click",
      guarded(() => show(company.id)),
    );
    name.append(
      button,
      el(
        "small",
        company.titles.slice(0, 2).join(" · ") ||
          "Attività da approfondire",
      ),
    );
    const status = el("td");
    status.append(
      el("span", labels[company.status], `badge ${company.status}`),
    );
    tr.append(
      name,
      el("td", company.category),
      el("td", locationPreview(company.locations)),
      el("td", String(company.opportunity_count)),
      status,
    );
    $("rows").append(tr);
  }
  if (!data.items.length) {
    const tr = el("tr");
    const td = el(
      "td",
      total
        ? "Nessuna azienda in questa pagina."
        : "Nessun risultato. Cambia i filtri oppure importa i dati dalla CLI.",
    );
    td.colSpan = 5;
    tr.append(td);
    $("rows").append(tr);
  }
  $("previous").disabled = offset === 0;
  $("next").disabled = offset + config.page_size >= total;
  $("page").textContent = total
    ? `${offset + 1}–${Math.min(offset + config.page_size, total)} di ${total}`
    : "0 risultati";
}
async function show(id) {
  selected = id;
  const company = await api("/api/company/" + id);
  if (selected !== id) return;
  const panel = $("detail");
  panel.replaceChildren();
  panel.append(
    el("h2", company.name),
    el("span", labels[company.status], `badge ${company.status}`),
  );
  panel.append(el("p", "Categoria: " + company.category));
  panel.append(el("p", (company.category_method === "chat" ? "Assegnata dalla chat. " : company.category_method === "local_llm" ? "Suggerita dal modello locale. " : company.category_method === "rules" ? "Suggerita da regole. " : "") + company.category_reason, "muted"));
  if (company.sectors) panel.append(el("p", "Settori dalla fonte: " + company.sectors, "muted"));
  if (company.website) {
    const website = el("p");
    website.append(link(company.website, "Sito aziendale"));
    panel.append(website);
  }
  panel.append(
    el(
      "p",
      company.description || "Descrizione aziendale da approfondire in chat.",
    ),
  );
  panel.append(
    el(
      "p",
      `Ultima osservazione: ${date(company.last_seen)}. ${company.opportunities.length} opportunità osservate.`,
      "muted",
    ),
  );
  const form = el("form");
  const statusLabel = el("label", "Stato azienda");
  const select = el("select");
  select.id = "detail-status";
  for (const [value, label] of Object.entries(labels)) {
    const option = el("option", label);
    option.value = value;
    select.append(option);
  }
  select.value = company.status;
  statusLabel.append(select);
  const noteLabel = el("label", "Motivo o nota");
  const reasonLabel = el("label", "Motivo della decisione");
  const reason = el("select");
  reason.id = "feedback-reason";
  for (const [value, text] of Object.entries(config.feedback_reasons)) {
    const option = el("option", text); option.value = value; reason.append(option);
  }
  reason.value = "other";
  reasonLabel.append(reason);
  const untilLabel = el("label", "Riproponi dal giorno (per Non ora)");
  const until = el("input"); until.type = "date"; until.id = "feedback-until"; untilLabel.append(until);
  const note = el("textarea");
  note.rows = 2;
  note.id = "detail-note";
  noteLabel.append(note);
  const actions = el("div", undefined, "actions");
  const save = el("button", "Salva decisione", "primary");
  save.type = "submit";
  actions.append(save);
  form.append(statusLabel, reasonLabel, untilLabel, noteLabel, actions);
  form.addEventListener(
    "submit",
    guarded(async (e) => {
      e.preventDefault();
      await api("/api/feedback", {
        company_id: id,
        status: select.value,
        note: note.value,
        reason: reason.value,
        until_date: until.value,
      });
      message("Decisione salvata.");
      await show(id);
      await load();
    }),
  );
  panel.append(form);
  if (company.assessment) {
    panel.append(el("h3", "Valutazione dalla chat"));
    if (company.assessment.stale)
      panel.append(
        el("p", "Dati o preferenze cambiati: rivalutare in chat.", "muted"),
      );
    panel.append(el("p", company.assessment.reasoning));
    for (const missing of company.assessment.missing_information || [])
      panel.append(el("p", "Da verificare: " + missing));
  }
  if (company.evidence.length) {
    panel.append(el("h3", "Ricerca e fonti"));
    for (const evidence of company.evidence) {
      const p = el("p", evidence.note + " ");
      p.append(
        link(evidence.source_url, "Fonte"),
        el("small", " · " + date(evidence.observed_at)),
      );
      panel.append(p);
    }
  }
  panel.append(el("h3", "Opportunità associate"));
  for (const job of company.opportunities) {
    const block = el("article", undefined, "opportunity");
    block.append(
      el("h3", job.title),
      locationDetails(job.locations),
    );
    block.append(
      el(
        "p",
        [job.remote_policy, job.employment_type, job.seniority]
          .filter(Boolean)
          .join(" · ") || "Condizioni non specificate",
        "muted",
      ),
    );
    const s = job.salary;
    if (s.min !== null || s.max !== null || s.raw_text)
      block.append(
        el(
          "p",
          "Salario di questo ruolo: " +
            (s.raw_text ||
              [
                s.min === null ? "" : "da " + s.min.toLocaleString("it-IT"),
                s.max === null ? "" : "fino a " + s.max.toLocaleString("it-IT"),
                s.currency || "valuta non indicata",
                s.period || "periodo non indicato",
              ].join(" ")),
        ),
      );
    const links = el("p");
    links.append(link(job.application_url, "Apri annuncio"));
    block.append(links);
    for (const source of job.sources) {
      const p = el(
        "p",
        source.source + " · " + date(source.observed_at) + " · ",
        "muted",
      );
      p.append(link(source.source_url, "Fonte"));
      block.append(p);
    }
    const details = el("details");
    details.append(
      el("summary", "Leggi descrizione"),
      description(job.formatted_description || job.description),
    );
    block.append(details);
    if (job.formatted_description) {
      const original = el("details");
      original.append(el("summary", "Testo originale · impaginazione con " + job.formatting_model), el("p", job.description));
      block.append(original);
    }
    if (job.selection) block.append(el("p", "Filtro iniziale: " + ({excluded: "ruolo escluso dalla shortlist", potential: "potenzialmente pertinente", review: "da verificare"}[job.selection.status]) + " · " + job.selection.reasons.join(", "), "muted"));
    if (job.selection?.requirements) {
      const facts = job.selection.requirements;
      for (const quote of [...facts.evidence, ...facts.eligibility_quotes]) block.append(el("blockquote", quote));
    }
    const roleReason = el("select"); roleReason.setAttribute("aria-label", "Motivo sul ruolo " + job.title);
    for (const [value, text] of Object.entries(config.feedback_reasons)) {
      if (value === "not_now") continue;
      const option = el("option", text); option.value = value; roleReason.append(option);
    }
    roleReason.value = "too_senior";
    const discardRole = el("button", "Scarta solo questo ruolo");
    discardRole.addEventListener("click", guarded(async () => {
      await api("/api/feedback", {company_id: id, opportunity_id: job.id, status: "discarded", reason: roleReason.value, note: "Decisione sul singolo ruolo"});
      message("Ruolo scartato; azienda conservata."); await show(id);
    }));
    block.append(roleReason, discardRole);
    const reject = el("button", "Segna solo questo ruolo da verificare");
    reject.addEventListener(
      "click",
      guarded(async () => {
        await api("/api/feedback", {
          company_id: id,
          opportunity_id: job.id,
          status: "review",
          note: "Ruolo da verificare; interesse aziendale invariato",
        });
        message("Annotazione sul ruolo salvata.");
        await show(id);
      }),
    );
    block.append(reject);
    panel.append(block);
  }
  if (company.feedback.length) {
    const history = el("details");
    history.append(el("summary", "Storico decisioni"));
    for (const event of company.feedback) {
      const row = el(
        "div",
        `${date(event.created_at)} · ${event.opportunity_id ? "Ruolo" : "Azienda"} · ${labels[event.status]} · ${config.feedback_reasons[event.reason] || ""} · ${event.until_date || ""} · ${event.note || "Senza nota"}`,
        "feedback-entry",
      );
      if (event.undone_at) row.append(el("small", " · Annullata"));
      else {
        const undo = el("button", "Annulla");
        undo.addEventListener(
          "click",
          guarded(async () => {
            await api("/api/undo", { event_id: event.id });
            await show(id);
            await load();
            message("Decisione annullata.");
          }),
        );
        row.append(undo);
      }
      history.append(row);
    }
    panel.append(history);
  }
  await load();
}
async function sources() {
  const stats = await api("/api/stats");
  $("source-list").replaceChildren();
  for (const [name, source] of Object.entries(config.sources)) {
    const row = el("div", undefined, "source");
    const text = el("div");
    text.append(
      el("strong", name),
      el("p", source.enabled ? "Disponibile per acquisizione" : source.reason),
    );
    row.append(text);
    const controls = el("div", undefined, "actions");
    const label = el("label", "Limite annunci");
    const input = el("input");
    input.type = "number";
    input.min = "1";
    input.max = String(config.max_jobs);
    input.value = String(Math.min(10, config.max_jobs));
    label.append(input);
    const run = el("button", "Aggiorna");
    run.disabled = !source.enabled || stats.collection.running;
    run.addEventListener(
      "click",
      guarded(async () => {
        await api("/api/collect", { source: name, limit: Number(input.value) });
        message("Acquisizione avviata. Lo stato è visibile qui sotto.");
        await sources();
      }),
    );
    controls.append(label, run);
    row.append(controls);
    $("source-list").append(row);
  }
  const state = stats.collection;
  $("collection-state").textContent = state.running
    ? "Raccolta in corso…"
    : state.result
      ? "Ultimo esito: " + state.result.status
      : "";
  $("runs").replaceChildren();
  if (!stats.runs.length)
    $("runs").append(
      el(
        "p",
        "Nessuna acquisizione live registrata. Gli import storici restano consultabili nelle aziende.",
      ),
    );
  for (const run of stats.runs) {
    const row = el("details", undefined, "run");
    row.append(
      el("summary", `${date(run.created_at)} · ${run.source} · ${run.status}`),
      el("pre", JSON.stringify(JSON.parse(run.detail), null, 2)),
    );
    $("runs").append(row);
  }
  if (state.running)
    setTimeout(() => sources().catch((e) => message(e.message, true)), 2500);
}
async function profile() {
  const data = await api("/api/profile");
  $("profile-text").textContent =
    data.profile || "Profilo originale non disponibile.";
  $("preferences").replaceChildren(
    ...data.preferences.map((p) => el("li", p.note)),
  );
}
async function exportCSV() {
  const items = [];
  for (let start = 0; start < total; start += 500) {
    const data = await api("/api/companies?" + params(start, 500));
    items.push(...data.items);
  }
  const safe = (v) => {
    let s = Array.isArray(v) ? v.join(" | ") : String(v ?? "");
    if (/^[=+@-]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  };
  const fields = [
    "name",
    "category",
    "status",
    "locations",
    "opportunity_count",
    "website",
    "last_seen",
  ];
  const csv = [
    fields.join(","),
    ...items.map((x) => fields.map((f) => safe(x[f])).join(",")),
  ].join("\r\n");
  const address = URL.createObjectURL(
    new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }),
  );
  const a = el("a");
  a.href = address;
  a.download = "jobhunter-aziende.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(address), 1000);
  message(`${items.length} aziende esportate.`);
}
/** Resume the persisted queue and expose explicit preference acceptance and outcome counts. */
async function loadQueue() {
  $("queue-items").textContent = "Preparazione della coda…";
  const [data, stats, proposals] = await Promise.all([api("/api/queue"), api("/api/metrics"), api("/api/proposals")]);
  $("queue-items").replaceChildren();
  if (!data.items.length) $("queue-items").append(el("p", "Nessuna azienda da proporre con i criteri attuali. Consulta l'archivio o aggiorna le fonti."));
  for (const company of data.items) {
    const row = el("article", undefined, "opportunity");
    const open = el("button", company.name);
    open.addEventListener("click", guarded(async () => {
      document.querySelector('[data-view="companies"]').click(); await show(company.id);
    }));
    row.append(open, el("p", company.category + " · " + company.role_count + " ruoli da valutare"));
    row.append(el("p", company.roles[0].change));
    row.append(el("p", company.roles[0].why.join(". ")));
    for (const role of company.roles) row.append(el("p", role.title));
    const research = el("button", "Prepara approfondimento in chat");
    research.addEventListener("click", guarded(async () => {
      const brief = await api("/api/research/" + company.id);
      const text = JSON.stringify(brief, null, 2);
      const area = el("textarea"); area.value = text; area.rows = 8; area.readOnly = true; area.setAttribute("aria-label", "Brief da copiare nella chat");
      row.append(area); area.focus(); area.select(); research.disabled = true;
    }));
    row.append(research); $("queue-items").append(row);
  }
  $("queue-metrics").textContent = `${stats.shown_companies} aziende proposte · ${stats.saved_or_contacted} interessanti o contattate · ${stats.discarded} scartate. ` + (stats.save_fraction_decided === null ? "Servono decisioni per misurare l'utilità." : `${Math.round(stats.save_fraction_decided*100)}% delle aziende decise è interessante.`);
  $("queue-proposals").replaceChildren();
  if (!proposals.items.length) $("queue-proposals").append(el("p", "Non ci sono ancora preferenze ricorrenti da proporre."));
  for (const proposal of proposals.items) {
    const row = el("p", "Escludere dalla coda il settore " + proposal.category + " · " + proposal.state + " ");
    for (const [state, label] of (proposal.state === "accepted" ? [["disabled", "Disattiva"]] : [["accepted", "Accetta"], ["dismissed", "Ignora"]])) {
      const button = el("button", label);
      button.addEventListener("click", guarded(async () => {await api("/api/proposal", {id: proposal.id, state}); await loadQueue();}));
      row.append(button);
    }
    $("queue-proposals").append(row);
  }
}

async function init() {
  config = await api("/api/bootstrap");
  token = config.token;
  for (const category of config.categories) {
    const option = el("option", category);
    option.value = category;
    $("category").append(option);
  }
  for (const source of Object.keys(config.sources)) {
    const option = el("option", source);
    option.value = source;
    $("source").append(option);
  }
  document.querySelectorAll("[data-view]").forEach((button) =>
    button.addEventListener(
      "click",
      guarded(async () => {
        for (const node of document.querySelectorAll("[data-view]"))
          node.removeAttribute("aria-current");
        button.setAttribute("aria-current", "page");
        for (const name of ["companies", "sources", "profile", "queue"])
          $(name + "-view").hidden = name !== button.dataset.view;
        if (button.dataset.view === "sources") await sources();
        if (button.dataset.view === "profile") await profile();
        if (button.dataset.view === "queue") await loadQueue();
      }),
    ),
  );
  $("filters").addEventListener(
    "submit",
    guarded(async (e) => {
      e.preventDefault();
      offset = 0;
      await load();
    }),
  );
  $("reset").addEventListener(
    "click",
    guarded(async () => {
      HTMLFormElement.prototype.reset.call($("filters"));
      offset = 0;
      await load();
    }),
  );
  $("previous").addEventListener(
    "click",
    guarded(async () => {
      offset = Math.max(0, offset - config.page_size);
      await load();
    }),
  );
  $("next").addEventListener(
    "click",
    guarded(async () => {
      offset += config.page_size;
      await load();
    }),
  );
  $("export").addEventListener("click", guarded(exportCSV));
  $("refresh-queue").addEventListener("click", guarded(loadQueue));
  $("preference-form").addEventListener(
    "submit",
    guarded(async (e) => {
      e.preventDefault();
      await api("/api/preference", { note: $("preference-note").value });
      $("preference-note").value = "";
      await profile();
      message("Preferenza aggiunta, disponibile anche nella chat.");
    }),
  );
  await load();
}
init().catch((error) => message(error.message, true));
