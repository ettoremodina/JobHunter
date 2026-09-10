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
    tier: $("tier-scope").value,
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
    // La colonna si chiama «azienda e attività»: se sappiamo cosa fa l'azienda, quella e'
    // l'attività. I titoli dei ruoli restano il ripiego di quando non lo sappiamo ancora.
    const activity = (company.description || "").trim();
    name.append(
      button,
      el(
        "small",
        activity ||
          company.titles.slice(0, 2).join(" · ") ||
          (company.archive_opportunity_count
            ? "Nessun ruolo che rientri nel filtro"
            : "Attività da approfondire"),
        activity ? "company-activity" : undefined,
      ),
    );
    const status = el("td");
    status.append(
      el("span", labels[company.status], `badge ${company.status}`),
    );
    const category = el("td");
    category.append(el("span", company.category || "Da classificare"));
    if (company.category && company.category !== "Da classificare") {
      category.append(el("small", categoryMethods[company.category_method] || company.category_method));
    }
    tr.append(
      name,
      category,
      el("td", company.tier_label ? company.tier : company.tier || "—"),
      el("td", locationPreview(company.locations)),
      el("td", company.archive_opportunity_count
        ? `${company.opportunity_count} di ${company.archive_opportunity_count}`
        : String(company.opportunity_count)),
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
    td.colSpan = 6;
    tr.append(td);
    $("rows").append(tr);
  }
  $("previous").disabled = offset === 0;
  $("next").disabled = offset + config.page_size >= total;
  $("page").textContent = total
    ? `${offset + 1}–${Math.min(offset + config.page_size, total)} di ${total}`
    : "0 risultati";
}
/** Lo stato di un ruolo come pastiglia colorata: si legge prima del testo che lo spiega. */
function verdictBadge(verdetto) {
  const [text, tone] = {tieni: ["Compatibile", "saved"], scarta: ["Scartato", "discarded"],
                        non_so: ["Da decidere", "review"]}[verdetto] || ["Senza verdetto", ""];
  return el("span", text, "badge " + tone);
}

/** L'ultima decisione ancora valida per ogni ruolo; `feedback` arriva già dal più recente. */
function roleDecisions(company) {
  const decided = new Map();
  for (const event of company.feedback)
    if (event.opportunity_id && !event.undone_at && !decided.has(event.opportunity_id))
      decided.set(event.opportunity_id, event.status);
  return decided;
}

/** Name the two axis verdicts and say how they compose the tier: the UI mirrors the code (DESIGN §9). */
function axisSummary(company) {
  const box = el("section", undefined, "axes");
  box.dataset.tier = company.tier || "";
  const verdict = company.company_verdict?.verdetto;
  const company_axis = {interessante: "Azienda interessante", non_interessante: "Fuori dalle categorie preferite",
                        evidenza_mancante: "Evidenza aziendale mancante"}[verdict] || "Asse azienda sconosciuto";
  const companyTone = {interessante: "saved", non_interessante: "discarded", evidenza_mancante: "review"}[verdict] || "";
  const roles = Object.values(company.opportunities || []).filter(job => job.verdict?.verdetto === "tieni");
  const role_axis = roles.length ? `${roles.length} ${roles.length === 1 ? "ruolo compatibile" : "ruoli compatibili"}` : "Nessun ruolo compatibile adesso";
  box.append(el("h3", company.tier_label || "Tier non calcolato"));
  const list = el("ul", undefined, "axis-list");
  for (const [name, value, tone, why] of [
      ["Asse azienda", company_axis, companyTone, company.company_verdict?.motivo || ""],
      ["Asse ruolo", role_axis, roles.length ? "saved" : "review", roles.map(job => job.title).slice(0, 3).join(" · ")]]) {
    const item = el("li");
    item.append(el("strong", name), el("span", value, "badge " + tone));
    if (why) item.append(el("small", why));
    list.append(item);
  }
  box.append(list);
  box.append(el("p", "I due assi si valutano separatamente; il tier è calcolato adesso e non è salvato.", "muted"));
  return box;
}

/** Show which judge decided a role and on what evidence, so a verdict is checkable months later. */
function roleVerdict(verdict) {
  const judges = {regex: "regex su titolo e descrizione", llm_remoto: "modello remoto"};
  const line = el("p", undefined, "muted");
  line.append(verdictBadge(verdict.verdetto));
  line.append(el("span", verdict.giudice ? ` deciso dal ${judges[verdict.giudice] || verdict.giudice}` : " nessun giudice ha ancora deciso"));
  if (verdict.motivo) line.append(el("span", " · " + verdict.motivo));
  if (verdict.primary) line.append(el("span", " · ruolo prioritario"));
  if (verdict.prove?.length) {
    const proof = el("details");
    proof.append(el("summary", "Prove del verdetto"));
    for (const quote of verdict.prove) proof.append(el("blockquote", String(quote)));
    line.append(proof);
  }
  return line;
}

/** Nome del sito senza schema; un indirizzo malformato non deve rompere la scheda. */
function hostname(address) {
  try { return new URL(address).hostname || address; } catch { return address; }
}

/** Compact key/value block: quattro paragrafi sciolti si leggono peggio di quattro righe allineate. */
function factList(pairs) {
  const list = el("dl", undefined, "detail-facts");
  for (const [key, value, note] of pairs) {
    if (!value) continue;
    list.append(el("dt", key));
    const dd = el("dd");
    dd.append(value instanceof Node ? value : el("span", value));
    if (note) {
      const hint = el("small", note);
      hint.title = note;
      dd.append(hint);
    }
    list.append(dd);
  }
  return list;
}

/** Un ruolo per volta, chiuso: la scheda si apre con l'elenco, non con il muro di testo. */
function opportunityBlock(job, company, decision, id, open) {
  const block = el("details", undefined, "opportunity");
  block.id = "job-" + job.id;
  block.open = open;
  block.dataset.verdict = job.verdict?.verdetto || "non_so";
  const head = el("summary");
  const line = el("span", undefined, "job-head");
  line.append(el("strong", job.title, "job-title"), verdictBadge(job.verdict?.verdetto));
  if (decision) line.append(el("span", labels[decision], `badge ${decision}`));
  head.append(line, el("small", [locationPreview(job.locations), date(job.last_seen_at)].filter(Boolean).join(" · ")));
  block.append(head);
  const body = el("div", undefined, "opportunity-body");
  block.append(body);
  body.append(locationDetails(job.locations));
  const s = job.salary;
  const conditions = [[job.remote_policy, job.employment_type, job.seniority].filter(Boolean).join(" · ") || "Condizioni non specificate"];
  if (s.min !== null || s.max !== null || s.raw_text)
    conditions.push(s.raw_text || [s.min === null ? "" : "da " + s.min.toLocaleString("it-IT"),
                                    s.max === null ? "" : "fino a " + s.max.toLocaleString("it-IT"),
                                    s.currency || "valuta non indicata", s.period || "periodo non indicato"].join(" "));
  body.append(el("p", conditions.join(" · "), "muted"));
  const links = el("p", undefined, "job-links");
  links.append(link(job.application_url, "Apri annuncio"));
  for (const source of job.sources) links.append(link(source.source_url, source.source + " · " + date(source.observed_at)));
  body.append(links);
  if (job.remote_summary) {
    body.append(el("p", job.remote_summary.summary));
    const fields = el("dl");
    for (const [key, label] of Object.entries(company.job_field_labels)) {
      const indices = job.remote_summary.fields[key];
      fields.append(el("dt", label));
      const value = el("dd", indices ? indices.map(i => job.remote_summary.facts[i].text).join("; ") : "Non indicato");
      if (indices) value.title = indices.map(i => job.remote_summary.facts[i].quote).join("\n");
      fields.append(value);
    }
    body.append(fields);
    for (const missing of job.remote_summary.missing_information) body.append(el("p", "Da verificare: " + missing, "muted"));
  }
  if (job.verdict?.verdetto) body.append(roleVerdict(job.verdict));
  const text = el("details");
  text.append(el("summary", "Leggi descrizione"), description(job.formatted_description || job.description));
  body.append(text);
  // Provenienza, verifiche e citazioni servono a controllare un verdetto, non a leggere l'annuncio.
  const proof = el("details");
  proof.append(el("summary", "Provenienza e verifiche"));
  if (job.formatted_description) {
    const original = el("details");
    original.append(el("summary", "Testo originale · impaginazione con " + job.formatting_model), el("p", job.description));
    proof.append(original);
  }
  if (job.possible_duplicates?.length) {
    const duplicates = el("p", "Possibile doppione: ");
    for (const other of job.possible_duplicates) {
      const anchor = el("a", "confronta annuncio ");
      anchor.href = "#job-" + other;
      duplicates.append(anchor);
    }
    proof.append(duplicates);
  }
  if (job.selection?.verification) {
    const verification = job.selection.verification;
    proof.append(el("p", `Informazioni: ${verification.description ? "descrizione disponibile" : "descrizione mancante"} · ${verification.experience_determined ? "esperienza obbligatoria determinata" : "esperienza da verificare"} · apertura da verificare`, "muted"));
  }
  if (job.selection?.requirements) {
    const facts = job.selection.requirements;
    if (facts.languages?.evidence.length) proof.append(el("p", "Requisiti linguistici: " + facts.languages.evidence.join(" · ")));
    if (job.description_check) proof.append(el("p", "Ultimo tentativo di recupero: " + date(job.description_check.checked_at) + " · " + ({available: "testo disponibile", blocked: "fonte temporaneamente bloccata", missing_page: "pagina non trovata", unsupported_parser: "testo non estraibile", temporary_error: "errore temporaneo"}[job.description_check.status] || "da verificare"), "muted"));
    for (const quote of [...facts.evidence, ...facts.eligibility_quotes]) proof.append(el("blockquote", quote));
  }
  body.append(proof);
  const roleReason = el("select");
  roleReason.setAttribute("aria-label", "Motivo sul ruolo " + job.title);
  for (const [value, text] of Object.entries(config.feedback_reasons)) {
    if (value === "not_now") continue;
    const option = el("option", text);
    option.value = value;
    roleReason.append(option);
  }
  roleReason.value = "too_senior";
  const actions = el("div", undefined, "actions job-actions");
  const discardRole = el("button", "Scarta solo questo ruolo");
  discardRole.addEventListener("click", guarded(async () => {
    await api("/api/feedback", {company_id: id, opportunity_id: job.id, status: "discarded", reason: roleReason.value, note: "Decisione sul singolo ruolo"});
    message("Ruolo scartato; azienda conservata.");
    await show(id);
  }));
  const reject = el("button", "Da verificare");
  reject.addEventListener("click", guarded(async () => {
    await api("/api/feedback", {company_id: id, opportunity_id: job.id, status: "review",
                                note: "Ruolo da verificare; interesse aziendale invariato"});
    message("Annotazione sul ruolo salvata.");
    await show(id);
  }));
  actions.append(roleReason, discardRole, reject);
  body.append(actions);
  return block;
}

async function show(id) {
  selected = id;
  const company = await api("/api/company/" + id);
  if (selected !== id) return;
  const panel = $("detail");
  panel.replaceChildren();
  const heading = el("div", undefined, "detail-heading");
  heading.append(el("h2", company.name), el("span", labels[company.status], `badge ${company.status}`));
  panel.append(heading);
  panel.append(axisSummary(company));
  const origin = company.category_method === "chat" ? "Assegnata dalla chat"
    : company.category_method === "remote" ? "Assegnata dal modello remoto"
    : company.category_method === "rules" ? "Suggerita da regole" : "";
  panel.append(factList([
    ["Categoria", company.category, [origin, company.category_reason].filter(Boolean).join(" · ")],
    ["Settori", company.sectors],
    ["Sito", company.website ? link(company.website, hostname(company.website)) : ""],
    ["Osservata", date(company.last_seen), `${company.opportunities.length} opportunità osservate`]]));
  panel.append(el("p", company.remote_summary?.summary || company.description || "Descrizione aziendale da approfondire in chat."));
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
  const note = el("textarea");
  note.rows = 2;
  note.id = "detail-note";
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
  // Le due scelte rapide coprono quasi tutto: il modulo completo resta a un clic, non a schermo.
  const decide = el("details", undefined, "decide");
  decide.append(el("summary", "Registra una decisione con motivo e nota"));
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
  noteLabel.append(note);
  const actions = el("div", undefined, "actions");
  const save = el("button", "Salva decisione", "primary");
  save.type = "submit";
  actions.append(save);
  form.append(statusLabel, reasonLabel, untilLabel, noteLabel, actions);
  form.addEventListener("submit", guarded(async (e) => {
    e.preventDefault();
    await api("/api/feedback", {company_id: id, status: select.value, note: note.value,
                                reason: reason.value, until_date: until.value});
    message("Decisione salvata.");
    await show(id);
    await load();
  }));
  decide.append(form);
  panel.append(decide);
  if (company.assessment || company.evidence.length) {
    const research = el("details");
    research.append(el("summary", "Valutazione dalla chat e fonti"));
    if (company.assessment) {
      if (company.assessment.stale) research.append(el("p", "Dati o preferenze cambiati: rivalutare in chat.", "muted"));
      research.append(el("p", company.assessment.reasoning));
      for (const missing of company.assessment.missing_information || []) research.append(el("p", "Da verificare: " + missing));
    }
    for (const evidence of company.evidence) {
      const p = el("p", evidence.note + " ");
      p.append(link(evidence.source_url, "Fonte"), el("small", " · " + date(evidence.observed_at)));
      research.append(p);
    }
    panel.append(research);
  }
  const scope = $("eligibility").value;
  const tierScope = $("tier-scope").value;
  // Un tier filtra le aziende, ma la scheda deve mostrare i ruoli che quel tier riguarda:
  // elencare gli scartati di un'azienda in Tier A e' rumore. Stessa regola lato server in search().
  const visible = company.opportunities.filter(job =>
    (!scope || job.selection?.status === scope) &&
    (!tierScope || tierScope === "B-attesa" || job.verdict?.verdetto !== "scarta"));
  const hidden = company.opportunities.length - visible.length;
  // Compatibili in cima: l'ordine per data mette gli scarti davanti a ciò che conta.
  const rank = {tieni: 0, non_so: 1, scarta: 2};
  visible.sort((a, b) => (rank[a.verdict?.verdetto] ?? 1) - (rank[b.verdict?.verdetto] ?? 1));
  const tally = {tieni: 0, non_so: 0, scarta: 0};
  for (const job of visible) tally[job.verdict?.verdetto || "non_so"]++;
  const rolesHeading = el("div", undefined, "roles-heading");
  rolesHeading.append(el("h3", `Opportunità associate · ${visible.length}`));
  for (const [key, one, many] of [["tieni", "compatibile", "compatibili"], ["non_so", "da decidere", "da decidere"], ["scarta", "scartato", "scartati"]])
    if (tally[key]) rolesHeading.append(el("span", `${tally[key]} ${tally[key] === 1 ? one : many}`, "badge " + {tieni: "saved", non_so: "review", scarta: "discarded"}[key]));
  panel.append(rolesHeading);
  if (hidden) {
    const reasons = [];
    if (scope) reasons.push({potential: "compatibili con i filtri", review: "da verificare", excluded: "esclusi dai filtri"}[scope]);
    if (tierScope && tierScope !== "B-attesa") reasons.push("non scartati sull'asse ruolo");
    const filtered = el("p", `${visible.length} di ${company.opportunities.length} ruoli: sono mostrati solo quelli ${reasons.join(" e ")}. `, "muted");
    const showAll = el("button", "Mostra tutti i ruoli");
    showAll.addEventListener("click", guarded(async () => { $("eligibility").value = ""; $("tier-scope").value = ""; offset = 0; await show(id); }));
    filtered.append(showAll);
    panel.append(filtered);
  }
  const decisions = roleDecisions(company);
  for (const job of visible)
    panel.append(opportunityBlock(job, company, decisions.get(job.id), id, visible.length === 1));
  if (company.feedback.length) {
    const history = el("details");
    history.append(el("summary", "Storico decisioni"));
    for (const event of company.feedback) {
      const row = el("div", `${date(event.created_at)} · ${event.opportunity_id ? "Ruolo" : "Azienda"} · ${labels[event.status]} · ${config.feedback_reasons[event.reason] || ""} · ${event.until_date || ""} · ${event.note || "Senza nota"}`, "feedback-entry");
      if (event.undone_at) row.append(el("small", " · Annullata"));
      else {
        const undo = el("button", "Annulla");
        undo.addEventListener("click", guarded(async () => {
          await api("/api/undo", { event_id: event.id });
          await show(id);
          await load();
          message("Decisione annullata.");
        }));
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
    if (group.kind !== "source_evidence") {
      const prepare = el("button", "Prepara domanda per la chat");
      prepare.addEventListener("click", () => {
        const label = el("label", "Domanda e casi da copiare nella chat");
        const area = el("textarea"); area.rows = 6; area.readOnly = true;
        area.value = `Aiutami a chiarire questa preferenza: ${group.question}\nGruppo: ${group.id}. Riguarda ${group.opportunities} annunci in ${group.companies} aziende.\nEsempi:\n${group.examples.map(e => `${e.company}: ${e.title} (${e.id})`).join("\n")}\nDopo la mia risposta, mostra l'impatto della regola prima di applicarla. I fatti mancanti vanno verificati sulla fonte.`;
        label.append(area); article.append(label); area.focus(); area.select(); prepare.disabled = true;
      });
      article.append(prepare);
    }
    $(group.kind === "source_evidence" ? "queue-evidence" : "queue-questions").append(article);
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
  const counts = data.counts || {};
  $("queue-summary").textContent = `${data.items.length} aziende in questa sessione · Tier A ${counts.A || 0}, Tier B ${(counts["B-attesa"] || 0) + (counts["B-esperienza"] || 0)} in archivio. Requisiti e apertura degli annunci vanno verificati.`;
  // Tier A e Tier B non condividono la stessa coda: un annuncio scade, l'interesse per un'azienda no.
  const buckets = [["A", "Tier A · da guardare adesso", "Azienda interessante con almeno un ruolo compatibile."],
                   ["B", "Tier B · mappa senza urgenza", "Aziende da tenere d'occhio e ruoli che valgono da soli."]];
  const lists = {};
  for (const [key, title, note] of buckets) {
    const section = el("section", undefined, "queue-tier");
    const rows = el("div");
    section.append(el("h3", title), el("p", note, "muted"), rows);
    lists[key] = rows;
    $("queue-items").append(section);
  }
  for (const company of data.items) {
    const row = el("article", undefined, "queue-company");
    const heading = el("div", undefined, "queue-company-heading");
    const title = el("div");
    title.append(el("h3", company.name), el("p", `${company.tier_label} · ${company.category}`, "muted"));
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
    roles.append(el("h4", company.role_count ? `Ruoli da esplorare · ${company.role_count}` : "Nessun ruolo compatibile adesso"));
    const list = el("ul");
    for (const role of company.roles) list.append(el("li", role.title));
    roles.append(list);
    if (company.role_count > company.roles.length) roles.append(el("p", `Altri ${company.role_count - company.roles.length} ruoli nel dettaglio.`, "muted"));
    const why = el("section");
    why.append(el("h4", "Perché compare"));
    if (company.roles.length) {
      why.append(el("p", company.roles[0].change, "muted"));
      const reasons = el("ul");
      for (const reason of company.roles[0].why) reasons.append(el("li", reason));
      why.append(reasons, el("small", "Motivi riferiti al primo ruolo. Verifica gli altri nel dettaglio."));
    } else {
      why.append(el("p", company.company_verdict?.motivo || "Categoria fra quelle preferite.", "muted"),
                 el("small", "Nessun ruolo compatibile adesso: l'azienda resta da tenere d'occhio."));
    }
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
    row.append(actions); lists[company.tier === "A" ? "A" : "B"].append(row);
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
        for (const name of ["companies", "sources", "queue", "analytics", "pipeline"])
          $(name + "-view").hidden = name !== button.dataset.view;
        if (button.dataset.view === "sources") await sources();
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
  $('pipeline-close').addEventListener('click', () => $('pipeline-dialog').close());
  $('pipeline-form').addEventListener('submit', launchPipeline);
  $('pipeline-stop').addEventListener('click', () => stopPipeline(pipelineData?.controls.active?.id));
  $('pipeline-go-feedback').addEventListener('click', () => {
    $('pipeline-dialog').close();
    document.querySelector('[data-view="queue"]').click();
  });
  $("analytics-scope").addEventListener("change", guarded(loadAnalytics));
  await load();
}
init().then(() => {
  if (new URLSearchParams(location.search).get('view') === 'pipeline') document.querySelector('[data-view="pipeline"]').click();
}).catch((error) => message(error.message, true));

/** Render comparable counts with a shared denominator and no chart dependency. */
const categoryMethods = {rules: "da parole chiave", remote: "modello remoto",
  chat: "scelta tua", unknown: "nessuna corrispondenza"};

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

let pipelineData = null, pipelineStep = null, pipelineTimer = null;
const pipelineStates = {running: 'In esecuzione', success: 'Terminato', partial: 'Parziale: controlla e riprova', failed: 'Errore: da riprovare', interrupted: 'Interrotto: da riprendere'};

/** Read pipeline evidence without starting collection, filtering or model work. */
async function loadPipeline() {
  const button = $('refresh-pipeline');
  if (button.disabled) return;
  button.disabled = true;
  $('pipeline-status').textContent = 'Lettura dello stato della pipeline…';
  try {
    const data = await api('/api/pipeline');
    pipelineData = data;
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
    for (const [index, step] of data.steps.filter(s => !['analytics', 'categories'].includes(s.id)).entries()) {
      const row = el('li', undefined, 'pipeline-step');
      const action = data.controls.actions[step.id];
      const active = data.controls.active;
      const running = active && (active.step === step.id || active.detail.phase === step.id || step.id === 'normalization' && (active.step === 'collection' || active.detail.phase === 'collection'));
      const stale = action?.needs_update || step.stale;
      const last = action?.last_run;
      const retry = last && ['partial', 'failed', 'interrupted'].includes(last.status);
      row.dataset.state = running ? 'running' : retry ? 'retry' : stale ? 'stale' : step.state;
      const card = el('button', undefined, 'pipeline-card');
      card.type = 'button';
      if (running) card.setAttribute('aria-current', 'step');
      card.setAttribute('aria-label', 'Apri ' + step.title);
      card.append(el('span', `${index + 1}. ${step.title}`, 'pipeline-title'));
      const state = running ? active.cancel_requested ? 'Arresto richiesto' : 'In esecuzione' : retry ? pipelineStates[last.status] : stale ? 'Dati cambiati: da aggiornare' : last?.parameters.mode === 'preview' ? 'Anteprima disponibile' : step.total === null ? 'Su richiesta' : {complete: 'Copertura completa', partial: 'Copertura parziale', missing: 'Da elaborare', empty: 'Nessun dato'}[step.state] || 'Su richiesta';
      card.append(el('span', state, 'pipeline-state'));
      card.append(stepMeasure(step));
      const stamp = action?.last_success || step.updated_at;
      // Mentre gira, l'orario che conta e' quello di avvio, e lo scrive il pannello qui sotto.
      if (!running) card.append(el('span', stamp ? 'Ultima esecuzione: ' + pipelineDate(stamp) : 'Mai eseguito da questo pannello', 'pipeline-time'));
      card.append(el('span', action ? 'Parametri e avvio' : 'Dettagli del passaggio', 'pipeline-affordance'));
      card.addEventListener('click', () => openPipeline(step.id));
      row.append(card);
      if (action || step.id === 'normalization') {
        const stop = el('button', running && active.cancel_requested ? 'Arresto richiesto' : 'Interrompi', 'pipeline-stop');
        stop.type = 'button'; stop.setAttribute('aria-label', 'Interrompi ' + step.title);
        stop.disabled = !data.controls.supports_stop || !running || Boolean(active?.cancel_requested);
        // A stop control for a step that is not running is dead weight on every card.
        stop.hidden = !running;
        stop.addEventListener('click', () => stopPipeline(active.id));
        row.append(stop);
      }
      list.append(row);
    }
    renderPipelineFunnel(data.funnel);
    renderJourney(data.funnel?.percorso);
    renderPipelineActivity();
    $('pipeline-runs').replaceChildren();
    for (const run of data.controls.history) {
      const button = el('button', `${data.controls.actions[run.step]?.label || 'Sequenza'} · ${pipelineStates[run.status]} · ${pipelineDate(run.started_at)}`, 'pipeline-history');
      button.addEventListener('click', () => openPipeline(run.step === 'sequence' ? run.detail.steps?.[0]?.step || run.detail.phase || 'remote' : run.step, run));
      $('pipeline-runs').append(button);
    }
    if (!data.runs.length) $('pipeline-runs').append(el('p', 'Nessuna esecuzione registrata. I dati importati possono comunque essere presenti.'));
    for (const run of data.runs) $('pipeline-runs').append(el('p', `${run.source}${run.task ? ' · ' + run.task : ''} · ${states[run.status] || run.status} · ${pipelineDate(run.created_at)}`));
  } catch (error) {
    $('pipeline-status').textContent = 'Stato non disponibile. Riprova con Aggiorna stato. Gli eventuali dati sotto sono della lettura precedente.';
    throw error;
  } finally {
    button.disabled = false;
    clearTimeout(pipelineTimer);
    if (!$('pipeline-view').hidden || $('pipeline-dialog').open) pipelineTimer = setTimeout(() => {
      if (!$('pipeline-view').hidden || $('pipeline-dialog').open) loadPipeline().catch(error => message(error.message, true));
    }, (pipelineData?.controls.poll_seconds || 5) * 1000);
  }
}

/** Render a proportional bar with a single labelled count for every share, including zeroes. */
function pipelineShares(title, total, parts) {
  const n = value => Number(value || 0).toLocaleString('it-IT');
  const section = el('div', undefined, 'pipeline-shares');
  if (title) {
    const heading = el('div', undefined, 'share-heading');
    heading.append(el('strong', title), el('span', n(total)));
    section.append(heading);
  }
  const ns = 'http://www.w3.org/2000/svg';
  const bar = document.createElementNS(ns, 'svg');
  bar.setAttribute('viewBox', '0 0 1000 24');
  bar.setAttribute('preserveAspectRatio', 'none');
  bar.setAttribute('class', 'funnel-chart');
  bar.setAttribute('aria-hidden', 'true');
  let x = 0;
  for (const [color, label, value] of parts) {
    const rect = document.createElementNS(ns, 'rect');
    const width = total > 0 ? value / total * 1000 : 0;
    for (const [key, val] of Object.entries({x, y: 0, width, height: 24, class: 'seg ' + color})) rect.setAttribute(key, val);
    bar.append(rect);
    x += width;
  }
  section.append(bar);
  const legend = el('ul', undefined, 'funnel-legend');
  for (const [color, label, value] of parts) {
    const item = el('li');
    const swatch = el('span', undefined, 'swatch ' + color);
    swatch.setAttribute('aria-hidden', 'true');
    item.append(swatch, el('span', label), el('strong', n(value), 'count'));
    legend.append(item);
  }
  section.append(legend);
  return section;
}

/** Keep coverage and its population together, without repeating the card's status. */
function stepMeasure(step) {
  const box = el('div', undefined, 'pipeline-measure');
  if (step.total !== null && step.total > 0) {
    // Un passaggio che si ferma per informazione mancante ha tre quote, non due: le dichiara lui.
    box.append(pipelineShares(step.measure || '', step.total, step.parts || [
      ['seg-in', step.done_label, step.done],
      ['seg-pending', step.rest_label || 'Da elaborare', step.pending ?? Math.max(0, step.total - step.done)]]));
  } else {
    box.append(el('strong', Number(step.done || 0).toLocaleString('it-IT') + ' ' + step.done_label));
  }
  // Un passaggio che lavora su due popolazioni ne mostra due: una sola barra racconta meta' lavoro.
  for (const bar of step.extra || []) box.append(pipelineShares(bar.title, bar.total, bar.parts));
  if (step.scope) box.append(el('small', step.scope));
  return box;
}

/** Show the independent axes and derived tiers once, with labels attached to each chart. */
function renderPipelineFunnel(funnel) {
  const area = $('pipeline-funnel');
  area.replaceChildren(el('h3', 'Due assi indipendenti → Tier'));
  if (!funnel) {
    area.append(el('p', 'Funnel non disponibile. Riavvia il server al termine dell’esecuzione.'));
    return;
  }
  area.append(
    pipelineShares('Asse ruolo · annunci', funnel.archive.jobs, [
      ['seg-keep', 'Compatibili', funnel.ruolo.tieni],
      ['seg-review', 'Da decidere', funnel.ruolo.non_so],
      ['seg-exclude', 'Scartati', funnel.ruolo.scarta]]),
    pipelineShares('Asse azienda · aziende', funnel.archive.companies, [
      ['seg-keep', 'Interessanti', funnel.azienda.interessante],
      ['seg-pending', 'Evidenza mancante', funnel.azienda.evidenza_mancante],
      ['seg-exclude', 'Fuori preferenze', funnel.azienda.non_interessante]]),
    pipelineShares('Tier · aziende', funnel.archive.companies, [
      ['seg-keep', 'A · azienda e ruolo sì', funnel.tier.A],
      ['seg-in', 'B · attesa di un ruolo', funnel.tier['B-attesa']],
      ['seg-review', 'B · solo esperienza', funnel.tier['B-esperienza']],
      ['seg-pending', 'Evidenza mancante', funnel.tier['evidenza-mancante']],
      ['seg-exclude', 'Scarto', funnel.tier.scarto]]));
  const details = el('details');
  details.append(el('summary', 'Origine dei giudizi'));
  if (funnel.giudici) details.append(pipelineShares('Giudici · annunci', funnel.archive.jobs,
    [['regex', 'Regex', 'seg-in'],
     ['llm_remoto', 'Modello remoto', 'seg-review'], ['nessuno', 'Senza giudizio', 'seg-pending']]
      .map(([key, label, color]) => [color, label, funnel.giudici[key] || 0])));
  if (funnel.basis) details.append(el('p', funnel.basis, 'muted'));
  area.append(details);
}

/** Il percorso come imbuto: ogni tappa larga quanto e' arrivato fin li', e chi esce esce alla sua tappa.

Le quote di ogni tappa sono complementari, e il totale di una tappa e' la quota che prosegue di quella
prima: il restringimento del disegno e' il dato, non una decorazione. La legenda ripete ogni numero,
percio' il colore non porta mai da solo un'informazione.
*/
function renderJourney(stages) {
  const area = $('pipeline-journey');
  area.replaceChildren();
  if (!stages?.length) {
    area.append(el('p', 'Percorso non disponibile. Riavvia il server al termine dell’esecuzione.'));
    return;
  }
  const n = value => Number(value || 0).toLocaleString('it-IT');
  const ns = 'http://www.w3.org/2000/svg';
  const node = (tag, attributes, text) => {
    const element = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const W = 1000, BAR = 30, GAP = 40, TOP = 20, ROW = BAR + GAP;
  const chart = node('svg', {viewBox: `0 0 ${W} ${TOP + stages.length * ROW}`, class: 'funnel-chart', role: 'img'});
  const boxes = [];
  for (const [index, stage] of stages.entries()) {
    const y = TOP + index * ROW;
    const width = stage.scale > 0 ? Math.max(2, (stage.total / stage.scale) * W) : 0;
    const left = (W - width) / 2;
    boxes.push({left, width, y});
    // Il flusso collega solo tappe della stessa unita': un annuncio non "diventa" un'azienda.
    const before = boxes[index - 1];
    if (before && !stage.unit_change)
      chart.append(node('polygon', {class: 'flow', points:
        `${before.left},${before.y + BAR} ${before.left + before.width},${before.y + BAR} ${left + width},${y} ${left},${y}`}));
    chart.append(node('text', {x: 0, y: y - 7, class: 'stage-label'}, stage.title));
    chart.append(node('text', {x: W, y: y - 7, class: 'stage-total', 'text-anchor': 'end'}, `${n(stage.total)} ${stage.unit}`));
    chart.append(node('rect', {x: left, y, width, height: BAR, rx: 5, class: 'track'}));
    let x = left;
    for (const [color, label, value] of stage.parts) {
      const part = stage.total > 0 ? (value / stage.total) * width : 0;
      if (part >= 1) {
        chart.append(node('rect', {x, y, width: part, height: BAR, class: 'seg ' + color}));
        if (part >= 70) chart.append(node('text', {x: x + part / 2, y: y + BAR / 2 + 4, class: 'seg-label', 'text-anchor': 'middle'}, n(value)));
      }
      x += part;
    }
  }
  chart.append(node('title', {}, stages.map(stage =>
    `${stage.title}: ${n(stage.total)} ${stage.unit}, di cui ` +
    stage.parts.map(([, label, value]) => `${n(value)} ${label}`).join(', ')).join('. ')));
  const scroll = el('div', undefined, 'journey-scroll');
  scroll.append(chart);
  area.append(scroll);
  for (const stage of stages) {
    area.append(pipelineShares(`${stage.title} · ${stage.unit}`, stage.total, stage.parts));
    if (stage.note) area.append(el('p', stage.note, 'muted'));
  }
}

/** Read the two live progress shapes: companies for Qwen, single records for the descriptions. */
function progressOf(detail) {
  if (detail.total_companies !== undefined) return {done: detail.completed_companies || 0, total: detail.total_companies, label: 'Aziende attraversate'};
  if (detail.phase === 'descriptions' && detail.total) return {done: detail.done || 0, total: detail.total, label: 'Annunci elaborati'};
  if (detail.phase === 'company_profile' && detail.total) return {done: detail.done || 0, total: detail.total, label: 'Aziende esaminate'};
  return null;
}

/** Extrapolate the remaining time from the observed pace only; skipped records make it optimistic. */
function etaValue(detail, startedAt) {
  // Only a caller that knows the job is still running passes a start: a finished run has no time left.
  const step = startedAt ? progressOf(detail) : null;
  const start = Date.parse(detail.started_at || startedAt || '');
  if (!step || !step.done || step.done >= step.total || !start) return '';
  const seconds = ((Date.now() - start) / 1000) * (step.total - step.done) / step.done;
  if (!(seconds > 0)) return '';
  const hours = Math.floor(seconds / 3600), minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} h ${minutes} min` : seconds < 90 ? 'meno di due minuti' : `${minutes} min`;
}

/** Show run-only consumption as labelled facts; cached records never masquerade as paid requests. */
function pipelineProgress(detail, startedAt) {
  const c = detail.counts || {}, u = detail.usage || {}, b = detail.request_breakdown;
  const n = value => Number(value || 0).toLocaleString('it-IT');
  const modern = detail.task === 'company-batch';
  let rows;
  if (detail.phase === 'descriptions' && detail.total) {
    rows = [['Annunci elaborati', `${n(detail.done)} / ${n(detail.total)}`, `${n(detail.workers)} recuperi contemporanei`],
      ['Descrizioni salvate', n(detail.saved), `${n(detail.attempted)} pagine richieste`]];
    } else if (detail.phase === 'company_profile' && detail.total) {
    rows = [['Aziende esaminate', `${n(detail.done)} / ${n(detail.total)}`, 'ogni strada provata resta registrata'],
      ['Descrizioni trovate', n(detail.trovata), 'scheda aggregatore, sito aziendale o annuncio']];
  } else {
    rows = [
      ['Aziende attraversate', `${n(detail.completed_companies)} / ${n(detail.total_companies)}`, 'comprese quelle saltate senza chiamata'],
      ['Annunci valutati', modern ? `${n(c.evaluated_jobs)} / ${n(c.submitted_jobs)}` : b ? `${n(b.validated_jobs)} / ${n(b.submitted_jobs)}` : '—',
        modern || b ? 'risposte validate su annunci inviati' : 'non registrato dalla vecchia versione'],
      ['Nuovi esiti', modern ? `${n(c.kept_jobs)} · ${n(c.review_jobs)} · ${n(c.remote_excluded)}` : b ? `${n(b.keep)} · ${n(b.review)} · ${n(b.exclude)}` : n(c.remote_excluded),
        'da tenere · da verificare · esclusioni proposte'],
      ['Riusati dalla cache', modern ? n(c.cached_jobs) : n(c.cached), 'annunci già giudicati: nessuna nuova chiamata'],
      ['Saltati dai filtri locali', n(c.local_excluded), 'mai inviati, quindi mai pagati'],
      ['Chiamate API', modern ? `${n(c.api_calls)} per ${n(c.api_companies)} aziende` : b ? n(b.calls) : '—',
        modern && c.rejected_companies ? `${n(c.rejected_companies)} con errore o risposta rifiutata` : 'una per azienda'],
      ['Token consumati', n(u.total_tokens), `${n(u.prompt_tokens)} input + ${n(u.completion_tokens)} output`]];
    if (c.rejected_jobs) rows.push(['Annunci scartati dalla risposta', n(c.rejected_jobs), 'il resto della chiamata è stato salvato']);
    if (c.deferred_companies) rows.push(['Aziende oltre i limiti', n(c.deferred_companies), 'nessuna chiamata: da riprendere']);
  }
  const eta = etaValue(detail, startedAt);

  const panel = el('div', undefined, 'run-progress');
  const progress = progressOf(detail);
  if (progress) {
    panel.append(pipelineShares(progress.label, progress.total, [
      ['seg-in', 'Elaborati', progress.done], ['seg-pending', 'Rimanenti', Math.max(0, progress.total - progress.done)]]));
    rows = rows.filter(([label]) => label !== progress.label);
  }
  if (modern || b) {
    const outcomes = [['seg-keep', 'Da tenere', modern ? c.kept_jobs : b.keep],
      ['seg-review', 'Da verificare', modern ? c.review_jobs : b.review],
      ['seg-exclude', 'Esclusioni proposte', modern ? c.remote_excluded : b.exclude]];
    panel.append(pipelineShares('Nuovi esiti · annunci', outcomes.reduce((sum, part) => sum + (part[2] || 0), 0), outcomes));
    rows = rows.filter(([label]) => label !== 'Nuovi esiti');
  }
  const list = el('dl', undefined, 'run-facts');
  if (detail.company_name) panel.append(el('p', detail.company_name));
  if (eta) panel.append(el('p', 'Tempo rimasto stimato: ' + eta, 'muted'));
  for (const [label, value, hint] of rows) {
    list.append(el('dt', label));
    const cell = el('dd');
    cell.append(el('strong', String(value)), el('small', hint));
    list.append(cell);
  }
  const details = el('details');
  details.append(el('summary', 'Consumi e dettagli'), list);
  panel.append(details);
  return panel;
}

/** Keep the active run visible and refresh the dialog without discarding edited parameters. */
function renderPipelineActivity() {
  const active = pipelineData.controls.active;
  const area = $('pipeline-active');
  area.replaceChildren();
  if (active) {
    const detail = active.detail;
    area.append(el('strong', 'In esecuzione: ' + (pipelineData.controls.actions[detail.phase || active.step]?.label || 'Sequenza')));

    if (progressOf(detail)) area.append(pipelineProgress(detail, active.started_at));

    area.append(el('small', 'Avviato ' + pipelineDate(active.started_at) + '. Mantieni attivo il server della web app.'));
    if (!pipelineData.controls.supports_stop) area.append(el('p', 'Il comando Interrompi richiede il riavvio del server dopo questa esecuzione.'));
  } else if (pipelineData.collection_running) area.append(el('p', 'Raccolta avviata dalla pagina Fonti in corso. Attendi prima di avviare un altro passaggio.'));
  else {
    area.append(el('p', 'Nessun passaggio della web app in esecuzione. Seleziona una card per iniziare.'));
    const lastRemote = pipelineData.controls.history.find(run => run.detail.request_breakdown || run.detail.task === 'company-batch');
    if (lastRemote) {
      area.append(el('strong', 'Ultima esecuzione Qwen: ' + (pipelineStates[lastRemote.status] || lastRemote.status)));
      if (lastRemote.detail.mode === 'preview') area.append(el('p', 'Anteprima senza chiamate API. Apri il passaggio per i dettagli.', 'muted'));
      else area.append(pipelineProgress(lastRemote.detail));
    }
  }
  if ($('pipeline-dialog').open) {
    $('pipeline-start').disabled = Boolean(active || pipelineData.collection_running);
    const ownRun = active && (active.step === pipelineStep || active.detail.phase === pipelineStep || pipelineStep === 'normalization' && (active.step === 'collection' || active.detail.phase === 'collection'));
    $('pipeline-stop').disabled = !pipelineData.controls.supports_stop || !ownRun || Boolean(active?.cancel_requested);
    $('pipeline-stop').textContent = ownRun && active.cancel_requested ? 'Arresto richiesto' : 'Interrompi';
    const last = pipelineData.controls.actions[pipelineStep]?.last_run;
    $('pipeline-dialog-state').textContent = active ? 'Un passaggio è in esecuzione. Il risultato verrà aggiornato qui.' : last ? `${pipelineStates[last.status]} · ${pipelineDate(last.finished_at || last.started_at)}` : '';
    if (last) pipelineResult(last);
  }
}

/** Render an inspectable saved outcome using text only, including costs when returned by the API. */
function pipelineResult(run) {
  const container = $('pipeline-last-result');
  container.replaceChildren();
  const detail = run.detail || {};
  if (detail.message) container.append(el('p', detail.message));
  if (detail.total_companies !== undefined) container.append(el('p', `${detail.completed_companies} / ${detail.total_companies} aziende attraversate, incluse quelle saltate`));
  if (detail.task === 'company-batch') {
    if (detail.mode === 'preview') container.append(el('p', `Anteprima: ${detail.counts?.planned_api_calls || 0} chiamate aziendali previste per ${detail.counts?.planned_jobs || 0} annunci. Nessuna chiamata API effettuata, nessun costo.`));
    else container.append(pipelineProgress(detail));
  } else if (detail.request_breakdown) { container.append(pipelineProgress(detail));
  } else if (detail.counts) container.append(el('p', `Richieste previste: ${detail.counts.selected || 0} · Risultati salvati: ${detail.counts.processed || 0} · Già in cache: ${detail.counts.cached || 0}`));
  if (detail.usage) container.append(el('p', `Token: ${detail.usage.total_tokens || 0} · Costo API: ${detail.usage.cost === undefined ? 'non restituito dal provider' : '$' + detail.usage.cost.toFixed(6)}`));
  if (detail.counts?.requests_without_cost) container.append(el('p', `Costo incompleto: ${detail.counts.requests_without_cost} risposte non riportano il costo. Controlla il consuntivo LLM remoto.`, 'error'));
  const details = el('details');
  details.append(el('summary', 'Risultato completo'), el('pre', JSON.stringify(detail, null, 2)));
  container.append(details);
}

/** Build native controls from server-provided limits, with an explicit paid mode. */
function pipelineField(key, scope, step) {
  const data = pipelineData.controls;
  const labels = {source: 'Fonte', limit: 'Numero massimo di annunci', workers: step === 'remote' ? 'Chiamate API contemporanee' : 'Recuperi contemporanei', all: 'Tutte le descrizioni recuperabili', refresh_stale: 'Aggiorna anche descrizioni scadute', force: 'Riprova gli errori, rispettando i blocchi della fonte', company_limit: 'Numero di aziende del campione', all_companies: "Tutte le aziende dell'archivio", mode: 'Modalità LLM remoto'};
  const label = el('label', labels[key]);
  let input;
  if (key === 'source' || key === 'mode') {
    input = el('select');
    const choices = key === 'mode' ? [['preview', 'Anteprima senza spesa'], ['execute', 'Esegui con API a pagamento']] : (step === 'collection' ? data.collection_sources : ['', ...data.description_sources]).map(v => [v, v || 'Tutte le fonti']);
    for (const [value, text] of choices) { const option = el('option', text); option.value = value; input.append(option); }
  } else {
    input = el('input');
    input.type = ['all', 'refresh_stale', 'force', 'all_companies', 'continue_after'].includes(key) ? 'checkbox' : 'number';
    if (input.type === 'number') {
      const scale = key === 'workers' ? (step === 'remote' ? 'remote_workers' : 'workers') : key === 'company_limit' ? 'remote' : step;
      input.min = '1'; input.max = String(data.limits[scale]);
      input.value = String(key === 'company_limit' ? data.defaults.company_limit : key === 'workers' ? data.defaults[scale] : Math.min(10, Number(input.max)));
      input.required = true;
    }
  }
  input.name = key; input.dataset.scope = scope; input.setAttribute('aria-label', labels[key]);
  label.append(input); return label;
}

/** Open one stage with its parameters, retry evidence and optional downstream sequence. */
function openPipeline(id, savedRun = null) {
  const step = pipelineData.steps.find(s => s.id === id);
  if (!step) return;
  pipelineStep = id;
  const action = pipelineData.controls.actions[id];
  $('pipeline-dialog-title').textContent = step.title;
  $('pipeline-dialog-note').textContent = action?.note || step.note;
  $('pipeline-error').textContent = '';
  $('pipeline-fields').replaceChildren(); $('pipeline-last-result').replaceChildren();
  $('pipeline-start').hidden = !action;
  $('pipeline-go-feedback').hidden = id !== 'feedback';
  if (action) {
    for (const key of action.fields) $('pipeline-fields').append(pipelineField(key, 'step', id));
    const sequence = pipelineData.controls.sequence;
    if (sequence.includes(id) && id !== sequence.at(-1)) {
      const label = el('label', 'Continua da qui con i passaggi successivi');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.name = 'continue_after';
      label.append(checkbox); $('pipeline-fields').append(label);
      const chain = el('div'); chain.hidden = true;
      chain.append(el('p', sequence.slice(sequence.indexOf(id)).map(k => pipelineData.controls.actions[k].label).join(' → ')));
      chain.append(el('p', 'I passaggi facoltativi vengono saltati. La sequenza si ferma su errore o risultato parziale. Il recupero successivo considera tutte le descrizioni eleggibili.'));
      if (id !== 'remote' && sequence.indexOf(id) < sequence.indexOf('remote')) {
        chain.append(el('h3', 'Passaggio LLM remoto della sequenza'));
        for (const key of ['company_limit', 'all_companies', 'mode']) chain.append(pipelineField(key, 'remote', 'remote'));
      }
      checkbox.addEventListener('change', () => { chain.hidden = !checkbox.checked; updatePipelineButton(); });
      $('pipeline-fields').append(chain);
    }
    $('pipeline-fields').onchange = updatePipelineButton;
  }
  if (!$('pipeline-dialog').open) $('pipeline-dialog').showModal();
  renderPipelineActivity(); updatePipelineButton();
  if (savedRun) pipelineResult(savedRun);
}

/** Name paid actions directly on the submit button, including paid work later in a sequence. */
function updatePipelineButton() {
  const form = $('pipeline-form');
  const sequence = form.elements.namedItem('continue_after')?.checked;
  const paid = [...form.querySelectorAll('select[name="mode"]')].some(input => input.value === 'execute' && (input.dataset.scope === 'step' || sequence));
  $('pipeline-start').textContent = paid ? sequence ? 'Avvia sequenza con API a pagamento' : 'Avvia API a pagamento' : sequence ? 'Avvia sequenza' : pipelineStep === 'remote' ? 'Prepara anteprima' : 'Avvia passaggio';
  for (const [toggle, number] of [['all', 'limit'], ['all_companies', 'company_limit']]) {
    for (const input of form.querySelectorAll(`input[name="${toggle}"]`)) {
      const field = form.querySelector(`input[name="${number}"][data-scope="${input.dataset.scope}"]`);
      if (field) field.disabled = input.checked;
    }
  }
}

/** Submit an allowlisted action; opening the dialog never starts work or calls an API. */
async function launchPipeline(event) {
  event.preventDefault();
  const button = $('pipeline-start'); button.disabled = true;
  $('pipeline-error').textContent = '';
  try {
    const values = {step: {}, remote: {}};
    for (const input of $('pipeline-fields').querySelectorAll('[data-scope]')) values[input.dataset.scope][input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
    await api('/api/pipeline/start', {step: pipelineStep, parameters: values.step, remote_parameters: values.remote, continue_after: $('pipeline-form').elements.namedItem('continue_after')?.checked || false});
    $('pipeline-dialog-state').textContent = 'Avvio registrato. Esecuzione in background.';
    await loadPipeline();
  } catch (error) {
    $('pipeline-error').textContent = error.message;
  } finally {
    button.disabled = Boolean(pipelineData?.controls.active || pipelineData?.collection_running);
  }
}

/** Stop the current stage and its remaining sequence without discarding saved results. */
async function stopPipeline(jobId) {
  if (!jobId) return;
  try {
    await api('/api/pipeline/stop', {job_id: jobId});
    message('Arresto richiesto. Le richieste già inviate possono terminare prima dello stop.');
    await loadPipeline();
    setTimeout(() => loadPipeline().catch(error => message(error.message, true)), 2000);
  } catch (error) { message(error.message, true); }
}
