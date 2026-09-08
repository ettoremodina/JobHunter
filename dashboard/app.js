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
    eligibility: $("eligibility").value,
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
/** Fetch one page while preventing repeated pagination clicks during the request. */
async function load() {
  const request = ++requestNumber;
  $("count").textContent = "Caricamento…";
  $("previous").disabled = true;
  $("next").disabled = true;
  let data;
  try {
    data = await api("/api/companies?" + params());
  } catch (error) {
    if (request === requestNumber) {
      $("count").textContent = "Ricerca non riuscita. Riprova con Cerca.";
      $("previous").disabled = offset === 0;
      $("next").disabled = offset + config.page_size >= total;
    }
    throw error;
  }
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
      company.remote_summary?.summary || company.description || "Descrizione aziendale da approfondire in chat.",
    ),
  );
  if (company.remote_summary?.summary) {
    const original = el("details");
    original.append(el("summary", "Fonti della sintesi aziendale"));
    if (company.description) original.append(description(company.description));
    for (const fact of company.remote_summary.facts) {
      const p = el("p", fact.quote + " ");
      if (fact.url) p.append(link(fact.url, "Fonte web"));
      original.append(p);
    }
    panel.append(original);
  }
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
  const companyChoices = el("div", undefined, "actions");
  for (const [text, status, reason] of [["Azienda non interessante", "discarded", "company_not_interested"], ["Nessun ruolo adatto adesso", "review", "no_current_roles"]]) {
    const button = el("button", text);
    button.addEventListener("click", guarded(async () => {
      await api("/api/feedback", {company_id: id, status, reason, note: note.value});
      message(reason === "no_current_roles" ? "Azienda rimandata: può tornare con nuovi ruoli o requisiti cambiati." : "Azienda esclusa dalle proposte. Puoi annullare la decisione nello storico.");
      await show(id); await load();
    }));
    companyChoices.append(button);
  }
  panel.append(companyChoices);
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
    block.id = "job-" + job.id;
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
    if (job.possible_duplicates?.length) {
      const duplicates = el("p", "Possibile doppione: ");
      for (const id of job.possible_duplicates) {
        const anchor = el("a", "confronta annuncio ");
        anchor.href = "#job-" + id;
        duplicates.append(anchor);
      }
      block.append(duplicates);
    }
    if (job.remote_summary) {
      block.append(el("p", job.remote_summary.summary));
      const fields = el("dl");
      for (const [key, label] of Object.entries(company.job_field_labels)) {
        const indices = job.remote_summary.fields[key];
        fields.append(el("dt", label));
        const value = el("dd", indices ? indices.map(i => job.remote_summary.facts[i].text).join("; ") : "Non indicato");
        if (indices) value.title = indices.map(i => job.remote_summary.facts[i].quote).join("\n");
        fields.append(value);
      }
      block.append(fields);
      for (const missing of job.remote_summary.missing_information) block.append(el("p", "Da verificare: " + missing, "muted"));
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
    if (job.selection?.verification) {
      const verification = job.selection.verification;
      block.append(el("p", `Informazioni: ${verification.description ? "descrizione disponibile" : "descrizione mancante"} · ${verification.experience_determined ? "esperienza obbligatoria determinata" : "esperienza da verificare"} · apertura da verificare`, "muted"));
    }
    if (job.selection?.requirements) {
      const facts = job.selection.requirements;
      if (facts.languages?.evidence.length) block.append(el("p", "Requisiti linguistici: " + facts.languages.evidence.join(" · ")));
      if (job.description_check) block.append(el("p", "Ultimo tentativo di recupero: " + date(job.description_check.checked_at) + " · " + ({available: "testo disponibile", blocked: "fonte temporaneamente bloccata", missing_page: "pagina non trovata", unsupported_parser: "testo non estraibile", temporary_error: "errore temporaneo"}[job.description_check.status] || "da verificare"), "muted"));
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
  $("archive-provenance").textContent = `${stats.companies.toLocaleString("it-IT")} aziende e ${stats.opportunities.toLocaleString("it-IT")} opportunità in archivio.` + (stats.first_import_at ? ` Primo inserimento: ${date(stats.first_import_at)}.` : " Archivio vuoto.");
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
    const label = el("label", "Annunci da acquisire in questo avvio");
    const input = el("input");
    input.type = "number";
    input.min = "1";
    input.max = String(config.max_jobs);
    input.value = String(Math.min(10, config.max_jobs));
    input.disabled = !source.enabled;
    label.append(input, el("small", `Massimo ${config.max_jobs} da questa pagina; non modifica i dati già salvati.`));
    const run = el("button", "Avvia raccolta limitata");
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
/** Separate reusable preference questions from facts that require source verification. */
function renderReviewQuestions(data) {
  $("queue-questions").replaceChildren();
  $("queue-evidence").replaceChildren();
  if (!data.questions.length) $("queue-questions").append(el("p", "Nessuna nuova domanda sulle preferenze. Restano disponibili le verifiche sulle fonti."));
  for (const group of [...data.questions, ...(data.evidence_tasks || [])]) {
    const article = el("article", undefined, "queue-company");
    article.append(el("h4", group.title), el("p", group.question), el("p", `${group.companies} aziende · ${group.opportunities} annunci interessati`, "muted"));
    const list = el("ul");
    for (const example of group.examples) {
      const item = el("li");
      const button = el("button", `${example.company} · ${example.title}`);
      button.addEventListener("click", guarded(async () => {
        document.querySelector('[data-view="companies"]').click();
        await show(example.company_id);
        $("detail").scrollIntoView({block: "start"});
      }));
      item.append(button); list.append(item);
    }
    article.append(list);
    if (group.kind === "preference") {
      const prepare = el("button", "Prepara domanda per la chat");
      prepare.addEventListener("click", () => {
        const label = el("label", "Domanda e casi da copiare nella chat");
        const area = el("textarea"); area.rows = 6; area.readOnly = true;
        area.value = `Aiutami a chiarire questa preferenza: ${group.question}\nGruppo: ${group.id}. Riguarda ${group.opportunities} annunci in ${group.companies} aziende.\nEsempi:\n${group.examples.map(e => `${e.company}: ${e.title} (${e.id})`).join("\n")}\nDopo la mia risposta, mostra l'impatto della regola prima di applicarla. I fatti mancanti vanno verificati sulla fonte.`;
        label.append(area); article.append(label); area.focus(); area.select(); prepare.disabled = true;
      });
      article.append(prepare);
    }
    $(group.kind === "preference" ? "queue-questions" : "queue-evidence").append(article);
  }
}

/** Resume the persisted queue and expose explicit preference acceptance and outcome counts. */
async function loadQueue() {
  $("queue-items").textContent = "Preparazione della coda…";
  const [data, stats, proposals, review] = await Promise.all([api("/api/queue"), api("/api/metrics"), api("/api/proposals"), api("/api/review-questions")]).catch(error => {
    $("queue-items").textContent = "Coda non disponibile. Riprova con Ricarica coda salvata.";
    throw error;
  });
  renderReviewQuestions(review);
  $("queue-items").replaceChildren();
  if (!data.items.length) $("queue-items").append(el("p", "Nessuna azienda da proporre con i criteri attuali. Consulta l'archivio o aggiorna le fonti."));
  $("queue-summary").textContent = `${data.items.length} aziende in questa sessione · ${data.total_eligible.toLocaleString("it-IT")} candidate secondo i filtri. Requisiti e apertura degli annunci vanno verificati.`;
  for (const company of data.items) {
    const row = el("article", undefined, "queue-company");
    const heading = el("div", undefined, "queue-company-heading");
    const title = el("div");
    title.append(el("h3", company.name), el("p", company.category, "muted"));
    const open = el("button", "Apri e valuta", "primary");
    open.setAttribute("aria-label", "Apri e valuta " + company.name);
    open.addEventListener("click", guarded(async () => {
      document.querySelector('[data-view="companies"]').click(); await show(company.id);
      $("detail").scrollIntoView({block: "start"});
      $("detail").setAttribute("tabindex", "-1"); $("detail").focus({preventScroll: true});
    }));
    heading.append(title, open); row.append(heading);
    const columns = el("div", undefined, "queue-columns");
    const roles = el("section");
    roles.append(el("h4", `Ruoli da esplorare · ${company.role_count}`));
    const list = el("ul");
    for (const role of company.roles) list.append(el("li", role.title));
    roles.append(list);
    if (company.role_count > company.roles.length) roles.append(el("p", `Altri ${company.role_count - company.roles.length} ruoli nel dettaglio.`, "muted"));
    const why = el("section");
    why.append(el("h4", "Perché compare"), el("p", company.roles[0].change, "muted"));
    const reasons = el("ul");
    for (const reason of company.roles[0].why) reasons.append(el("li", reason));
    why.append(reasons, el("small", "Motivi riferiti al primo ruolo. Verifica gli altri nel dettaglio."));
    columns.append(roles, why); row.append(columns);
    const research = el("button", "Prepara testo per la chat");
    research.addEventListener("click", guarded(async () => {
      const brief = await api("/api/research/" + company.id);
      const text = `Usa la skill jobhunter per approfondire ${brief.name} (ID ${company.id}).\nVerifica online i ruoli ancora aperti e la coerenza con il mio profilo. Distingui fatti verificati e informazioni mancanti.\n\nDomande:\n${brief.questions.map(q => "- " + q).join("\n")}\n\nLink da verificare:\n${[brief.website, ...brief.opportunities.map(o => o.url)].filter(Boolean).join("\n")}`;
      const label = el("label", "Testo da copiare nella chat di Codex");
      const area = el("textarea"); area.value = text; area.rows = 8; area.readOnly = true;
      area.setAttribute("aria-label", "Brief da copiare nella chat");
      label.append(area); row.append(label); area.focus(); area.select(); research.disabled = true;
    }));
    const actions = el("div", undefined, "queue-chat-actions");
    actions.append(research, el("small", "Prepara un testo. Non invia messaggi e non avvia un modello."));
    row.append(actions); $("queue-items").append(row);
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
        for (const name of ["companies", "sources", "profile", "queue", "analytics", "pipeline"])
          $(name + "-view").hidden = name !== button.dataset.view;
        if (button.dataset.view === "sources") await sources();
        if (button.dataset.view === "profile") await profile();
        if (button.dataset.view === "queue") await loadQueue();
        if (button.dataset.view === "analytics") await loadAnalytics();
        if (button.dataset.view === "pipeline") await loadPipeline();
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
  $("refresh-analytics").addEventListener("click", guarded(loadAnalytics));
  $("refresh-pipeline").addEventListener("click", guarded(loadPipeline));
  $("analytics-scope").addEventListener("change", guarded(loadAnalytics));
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

/** Render comparable counts with a shared denominator and no chart dependency. */
function metricTable(title, rows, total) {
  const section = el("section", undefined, "metric-section");
  section.append(el("h3", title));
  const table = el("table");
  const head = el("thead"), header = el("tr");
  for (const text of ["Gruppo", "Annunci", "% del totale"]) header.append(el("th", text));
  head.append(header); table.append(head);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    tr.append(el("td", row.label), el("td", row.count.toLocaleString("it-IT")), el("td", (total ? row.count * 100 / total : 0).toLocaleString("it-IT", {maximumFractionDigits: 1}) + "%"));
    body.append(tr);
  }
  table.append(body); section.append(table);
  return section;
}
let analyticsRequest = 0;
/** Refresh read-only distributions; stale responses cannot replace a newer scope. */
async function loadAnalytics() {
  const request = ++analyticsRequest;
  $("analytics-status").textContent = "Calcolo delle metriche in corso…";
  $("analytics-content").replaceChildren();
  try {
    const data = await api("/api/analytics?" + new URLSearchParams({eligibility: $("analytics-scope").value}));
    if (request !== analyticsRequest) return;
    $("analytics-status").textContent = `${data.total.toLocaleString("it-IT")} annunci · ${data.companies.toLocaleString("it-IT")} aziende · Aggiornato alle ${new Date(data.generated_at).toLocaleTimeString("it-IT")}`;
    const content = $("analytics-content");
    if (!data.total) { content.append(el("p", "Nessun annuncio in questa selezione.")); return; }
    const health = [
      {label: "Con categoria aziendale assegnata", count: data.health.categorized},
      {label: "Categoria da classificare", count: data.total - data.health.categorized},
      {label: "Con descrizione", count: data.health.with_description},
      {label: "Descrizione mancante", count: data.total - data.health.with_description},
      {label: "Paese non determinato", count: data.total - data.health.country_known},
      {label: "Salario mancante", count: data.total - data.health.with_salary},
      {label: "Data pubblicazione mancante", count: data.total - data.health.with_posted_date}
    ];
    content.append(el("p", "I conteggi riguardano annunci univoci. La categoria è il settore dell'azienda, non la mansione. Un campo presente non ne garantisce la correttezza o l'attualità.", "hint"));
    const grid = el("div", undefined, "metrics-grid");
    grid.append(metricTable("Completezza dei dati", health, data.total), metricTable("Esito dei filtri locali", data.selection.map(row => ({...row, label: {potential: "Potenzialmente compatibili", review: "Da verificare", excluded: "Esclusi"}[row.label]})), data.total), metricTable("Categorie aziendali", data.categories, data.total));
    content.append(grid, el("h3", "Distribuzione geografica"), el("p", "Ogni annuncio conta una volta per paese e continente: le percentuali possono superare il 100%. Si riconoscono nomi e codici espliciti; città isolate e sigle ambigue restano non determinate. Remoto non significa disponibile in tutto il mondo.", "hint"));
    const geography = el("div", undefined, "metrics-grid");
    geography.append(metricTable("Continenti", data.continents, data.total), metricTable("Paesi", data.countries, data.total));
    content.append(geography);
  } catch (error) {
    if (request === analyticsRequest) $("analytics-status").textContent = "Metriche non disponibili. Riprova con Aggiorna metriche.";
    throw error;
  }
}

/** Show exact local time and elapsed age without inventing missing history. */
function pipelineDate(value) {
  if (!value) return 'Data non registrata';
  const stamp = new Date(value);
  if (Number.isNaN(stamp.getTime())) return 'Data non disponibile';
  const hours = Math.max(0, Math.floor((Date.now() - stamp.getTime()) / 3600000));
  const age = hours >= 24 ? `${Math.floor(hours / 24)} giorni fa` : hours ? `${hours} ore fa` : "nell'ultima ora";
  return `${stamp.toLocaleString('it-IT')} · ${age}`;
}

/** Read pipeline evidence without starting collection, filtering or model work. */
async function loadPipeline() {
  const button = $('refresh-pipeline');
  if (button.disabled) return;
  button.disabled = true;
  $('pipeline-status').textContent = 'Lettura dello stato della pipeline…';
  try {
    const data = await api('/api/pipeline');
    $('pipeline-status').textContent = `${data.opportunities.toLocaleString('it-IT')} annunci · ${data.companies.toLocaleString('it-IT')} aziende · Stato letto alle ${new Date(data.generated_at).toLocaleTimeString('it-IT')}`;
    const workflow = $('pipeline-workflow');
    workflow.replaceChildren(el('h3', 'Ultimo workflow rilevato'));
    const states = {running: 'In esecuzione', not_running: 'Processo fermo, completamento non registrato', unconfirmed: 'Attività non confermata', success: 'Terminato', partial: 'Terminato con risultati parziali', failed: 'Fallito', not_found: 'Nessun report disponibile', unavailable: 'Report non disponibile'};
    workflow.append(el('p', [states[data.workflow.status] || data.workflow.status, data.workflow.phase_label].filter(Boolean).join(' · ')));
    if (data.workflow.started_at) workflow.append(el('p', 'Avviato: ' + pipelineDate(data.workflow.started_at), 'muted'));
    for (const warning of data.workflow.warnings || []) {
      if (warning.startsWith('Errore recupero dettagli:')) {
        const detail = el('details');
        detail.append(el('summary', 'Dettaglio dell’errore di recupero'), el('p', warning));
        workflow.append(detail);
      } else workflow.append(el('p', warning, 'error'));
    }
    if (data.oldest_observation) workflow.append(el('p', 'Osservazione più vecchia ancora in archivio: ' + pipelineDate(data.oldest_observation), 'muted'));
    const list = $('pipeline-steps');
    list.replaceChildren();
    for (const step of data.steps) {
      const row = el('li', undefined, 'pipeline-step');
      const content = el('div');
      const heading = el('div', undefined, 'pipeline-heading');
      heading.append(el('h3', step.title));
      if (step.total !== null) heading.append(el('span', {complete: 'Copertura completa', partial: 'Copertura parziale', missing: 'Da elaborare', stale: 'Da aggiornare', empty: 'Nessun dato'}[step.state], 'badge'));
      content.append(heading, el('p', step.note, 'pipeline-note'));
      const numbers = el('div', undefined, 'pipeline-evidence');
      numbers.append(el('strong', step.total === null ? `${step.done.toLocaleString('it-IT')} ${step.unit}` : `${step.done.toLocaleString('it-IT')} / ${step.total.toLocaleString('it-IT')} ${step.unit}`));
      if (step.pending) numbers.append(el('p', `${step.pending.toLocaleString('it-IT')} senza esito valido` + (step.stale ? `, di cui ${step.stale.toLocaleString('it-IT')} da aggiornare` : '')));
      numbers.append(el('p', 'Ultimo aggiornamento', 'muted'), el('p', pipelineDate(step.updated_at)));
      const layout = el('div', undefined, 'pipeline-columns');
      layout.append(content, numbers); row.append(layout); list.append(row);
    }
    $('pipeline-runs').replaceChildren();
    if (!data.runs.length) $('pipeline-runs').append(el('p', 'Nessuna esecuzione registrata. I dati importati possono comunque essere presenti.'));
    for (const run of data.runs) $('pipeline-runs').append(el('p', `${run.source}${run.task ? ' · ' + run.task : ''} · ${states[run.status] || run.status} · ${pipelineDate(run.created_at)}`));
  } catch (error) {
    $('pipeline-status').textContent = 'Stato non disponibile. Riprova con Aggiorna stato. Gli eventuali dati sotto sono della lettura precedente.';
    throw error;
  } finally {
    button.disabled = false;
  }
}
