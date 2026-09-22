"use strict";
// Il tema si decide prima del primo disegno: vince la scelta salvata, altrimenti il sistema.
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
document.documentElement.dataset.theme = remembered("theme") || (darkQuery.matches ? "dark" : "light");
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
    source: $("source").value,
    country: $("country").value,
    city: $("city").value,
    category: $("category").value,
    tier: $("tier-scope").value,
    sort: $("sort").value,
    offset: start,
    limit: size,
  });
}

/** Ruoli che rispondono ai filtri, per azienda della pagina corrente: li decide il server insieme ai risultati. */
const matchingRoles = new Map();

/** Preferenze di sola vista: se il browser blocca lo storage la pagina deve funzionare lo stesso. */
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* modalità privata o storage negato */ }
}
function remembered(key) {
  try { return localStorage.getItem(key); } catch { return null; }
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
  matchingRoles.clear();
  $("rows").replaceChildren();
  $("count").textContent = `${total.toLocaleString("it-IT")} aziende`;
  for (const company of data.items) {
    matchingRoles.set(company.id, company.matching_ids || []);
    const tr = el("tr");
    if (company.id === selected) tr.className = "selected";
    const name = el("td", undefined, "col-azienda");
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
    const status = el("td", undefined, "col-stato");
    status.append(
      el("span", labels[company.status], `badge ${company.status}`),
    );
    const category = el("td", undefined, "col-categoria");
    for (const label of companyCategories(company)) category.append(el("span", label, "tag"));
    if (company.categories?.length) {
      category.append(el("small", categoryMethods[company.category_method] || company.category_method));
    }
    tr.append(
      name,
      category,
      el("td", company.tier_label ? company.tier : company.tier || "—", "col-tier"),
      el("td", locationPreview(company.places?.length ? company.places : company.locations), "col-localita"),
      el("td", company.archive_opportunity_count
        ? `${company.opportunity_count} di ${company.archive_opportunity_count}`
        : String(company.opportunity_count), "col-ruoli"),
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
  const judges = {regex: "regex su titolo e descrizione", jev: "Jev"};
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
function opportunityBlock(job, company, decision, id, open, context = {}) {
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
  // La lingua dell'originale: quando l'annuncio passa viene tradotto e riassunto, e qui si perderebbe.
  const written = job.selection?.requirements?.written_in;
  if (written) {
    const line = el("p", `Lingua dell'annuncio: ${written.name}`, "muted");
    if (!written.known) line.append(el("span", "Lingua che non conosci", "badge review"));
    body.append(line);
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
    await show(id, context);
  }));
  const reject = el("button", "Da verificare");
  reject.addEventListener("click", guarded(async () => {
    await api("/api/feedback", {company_id: id, opportunity_id: job.id, status: "review",
                                note: "Ruolo da verificare; interesse aziendale invariato"});
    message("Annotazione sul ruolo salvata.");
    await show(id, context);
  }));
  actions.append(saveButton(company, id, job.id, context), roleReason, discardRole, reject);
  body.append(actions);
  return block;
}

/** Salvare è un campo a parte: mette da parte azienda o ruolo senza toccare i verdetti della pipeline. */
function saveButton(company, id, opportunityId, context) {
  const active = company.feedback.find(event => !event.undone_at && event.status === "saved"
    && (event.opportunity_id || null) === opportunityId);
  const what = opportunityId ? "il ruolo" : "l'azienda";
  const button = el("button", active ? "Rimuovi dalle salvate" : opportunityId ? "Salva ruolo" : "Salva azienda",
                    active ? undefined : "primary");
  button.addEventListener("click", guarded(async () => {
    if (active) await api("/api/undo", {event_id: active.id});
    else await api("/api/feedback", {company_id: id, opportunity_id: opportunityId, status: "saved",
                                     reason: "interesting", note: opportunityId ? "Ruolo salvato a mano" : "Azienda salvata a mano"});
    message(active ? `Tolto ${what} dalle salvate.` : `Salvato ${what}: lo trovi nella tab Salvate.`);
    await show(id, context);
    await (context.refresh || load)();
  }));
  return button;
}

async function show(id, context = {}) {
  // `context` dice dove disegnare e con quali filtri: la tab Salvate apre la scheda a casa sua,
  // senza i filtri della tab Aziende e senza ricaricarne la tabella.
  const {panel = $("detail"), filtered = true, refresh = load} = context;
  selected = id;
  const company = await api("/api/company/" + id);
  if (selected !== id) return;
  panel.replaceChildren();
  const heading = el("div", undefined, "detail-heading");
  heading.append(el("h2", company.name), el("span", labels[company.status], `badge ${company.status}`));
  panel.append(heading);
  panel.append(axisSummary(company));
  const origin = company.category_method === "chat" ? "Assegnata dalla chat"
    : company.category_method === "jev" ? "Assegnata dal giudice System One"
    : company.category_method === "rules" ? "Suggerita da regole" : "";
  panel.append(factList([
    ["Categorie", companyCategories(company).join(" · "), [origin, company.category_reason].filter(Boolean).join(" · ")],
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
      await show(id, context); await refresh();
    }));
    companyChoices.append(button);
  }
  companyChoices.prepend(saveButton(company, id, null, context));
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
    await show(id, context);
    await refresh();
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
  // Quali ruoli hanno fatto entrare l'azienda nei risultati lo ha già deciso il server con i filtri
  // della ricerca: la scheda mostra quelli, gli altri restano a portata di clic. Senza, filtrare per
  // «Milano» un'azienda con venti annunci apriva comunque tutti e venti.
  const matching = filtered ? matchingRoles.get(id) : null;
  const visible = matching ? company.opportunities.filter(job => matching.includes(job.id)) : [...company.opportunities];
  const others = matching ? company.opportunities.filter(job => !matching.includes(job.id)) : [];
  // Compatibili in cima: l'ordine per data mette gli scarti davanti a ciò che conta.
  const rank = {tieni: 0, non_so: 1, scarta: 2};
  const byVerdict = (a, b) => (rank[a.verdict?.verdetto] ?? 1) - (rank[b.verdict?.verdetto] ?? 1);
  visible.sort(byVerdict);
  others.sort(byVerdict);
  const tally = {tieni: 0, non_so: 0, scarta: 0};
  for (const job of visible) tally[job.verdict?.verdetto || "non_so"]++;
  const rolesHeading = el("div", undefined, "roles-heading");
  rolesHeading.append(el("h3", `Opportunità associate · ${visible.length}`));
  for (const [key, one, many] of [["tieni", "compatibile", "compatibili"], ["non_so", "da decidere", "da decidere"], ["scarta", "scartato", "scartati"]])
    if (tally[key]) rolesHeading.append(el("span", `${tally[key]} ${tally[key] === 1 ? one : many}`, "badge " + {tieni: "saved", non_so: "review", scarta: "discarded"}[key]));
  panel.append(rolesHeading);
  const decisions = roleDecisions(company);
  if (others.length)
    panel.append(el("p", `${visible.length} di ${company.opportunities.length} ruoli rispondono ai filtri attivi. Gli altri restano qui sotto.`, "muted"));
  for (const job of visible)
    panel.append(opportunityBlock(job, company, decisions.get(job.id), id, visible.length === 1, context));
  if (others.length) {
    const rest = el("details", undefined, "other-roles");
    rest.append(el("summary", `Altri ${others.length} ruoli dell'azienda, fuori dai filtri`));
    for (const job of others) rest.append(opportunityBlock(job, company, decisions.get(job.id), id, false, context));
    panel.append(rest);
  }
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
          await show(id, context);
          await refresh();
          message("Decisione annullata.");
        }));
        row.append(undo);
      }
      history.append(row);
    }
    panel.append(history);
  }
  await refresh();
}
/** Ricostruisce l'elenco città dopo un cambio di paese o un Azzera. */
let refillCities = () => {};

/** Paese e città vengono dalla mappatura: scegliere «Milano» prende anche gli annunci scritti «Milan» o «MI». */
async function setupPlaces() {
  // La prima lettura normalizza tutto l'archivio e ci mette un minuto: intanto la tabella è già
  // in pagina, e i due menu dicono che stanno arrivando invece di restare vuoti.
  const waiting = el("option", "Lettura delle località…");
  waiting.disabled = true;
  $("country").append(waiting);
  const data = await api("/api/places");
  waiting.remove();
  const cities = new Map(data.countries.map(country => [country.name, country.cities]));
  for (const country of data.countries) {
    const option = el("option", `${country.name} · ${nf(country.count)}`);
    option.value = country.name;
    $("country").append(option);
  }
  const fillCities = () => {
    const chosen = $("country").value;
    const list = chosen ? cities.get(chosen) || [] : [...cities.values()].flat();
    const previous = $("city").value;
    // Senza value esplicito un'opzione vale il proprio testo, e «Tutte le città» diventerebbe un filtro.
    const all = el("option", chosen ? "Tutte le città del paese" : "Tutte le città");
    all.value = "";
    $("city").replaceChildren(all);
    for (const city of [...list].sort((a, b) => b.count - a.count)) {
      const option = el("option", `${city.name} · ${nf(city.count)}`);
      option.value = city.name;
      $("city").append(option);
    }
    // Cambiando paese la città scelta prima quasi mai esiste ancora: meglio azzerarla che filtrare a vuoto.
    $("city").value = [...$("city").options].some(o => o.value === previous) ? previous : "";
  };
  refillCities = fillCities;
  fillCities();
  $("country").addEventListener("change", guarded(async () => { fillCities(); offset = 0; await load(); }));
  $("city").addEventListener("change", guarded(async () => { offset = 0; await load(); }));
}

/** Colonne facoltative della tabella: la prima resta sempre, è quella che apre l'azienda. */
const optionalColumns = [["categoria", "Categoria"], ["tier", "Tier"], ["localita", "Località"], ["ruoli", "Ruoli"], ["stato", "Stato"]];

function setupColumns() {
  const hidden = new Set((remembered("columns-hidden") || "").split(",").filter(Boolean));
  const apply = () => {
    for (const [key] of optionalColumns) $("results-table").classList.toggle("hide-" + key, hidden.has(key));
    remember("columns-hidden", [...hidden].join(","));
  };
  for (const [key, title] of optionalColumns) {
    const label = el("label", undefined, "column-choice");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !hidden.has(key);
    box.addEventListener("change", () => { box.checked ? hidden.delete(key) : hidden.add(key); apply(); });
    label.append(box, el("span", title));
    $("columns").append(label);
  }
  apply();
}

/** Larghezza della scheda azienda: si trascina il bordo, e la misura resta per la prossima visita. */
function setupResize() {
  const handle = $("detail-resize");
  const width = () => Number(remembered("detail-width")) || $("detail").getBoundingClientRect().width;
  const setWidth = (value) => {
    const size = Math.round(Math.min(Math.max(value, 320), Math.max(320, document.querySelector(".workspace").getBoundingClientRect().width - 420)));
    document.documentElement.style.setProperty("--detail-width", size + "px");
    handle.setAttribute("aria-valuenow", String(size));
    remember("detail-width", String(size));
  };
  if (remembered("detail-width")) setWidth(width());
  handle.addEventListener("pointerdown", (event) => {
    handle.setPointerCapture(event.pointerId);
    const move = (e) => setWidth(document.querySelector(".workspace").getBoundingClientRect().right - e.clientX);
    const stop = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", stop); };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    event.preventDefault();
  });
  handle.addEventListener("keydown", (event) => {
    const step = {ArrowLeft: 24, ArrowRight: -24}[event.key];
    if (!step) return;
    setWidth(width() + step);
    event.preventDefault();
  });
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
/** Le aziende messe da parte a mano: si aprono qui dentro, senza passare dai filtri della tab Aziende. */
async function loadSaved() {
  $("saved-items").textContent = "Lettura delle salvate…";
  const data = await api("/api/saved").catch(error => {
      $("saved-items").textContent = "Elenco non disponibile. Riprova con Ricarica.";
      throw error;
    });
  savedItems = data.items;
  $("saved-brief").disabled = !data.items.length;
  const roles = data.items.reduce((sum, company) => sum + company.roles.length, 0);
  $("saved-summary").textContent = data.items.length
    ? `${data.items.length} aziende salvate · ${roles} ruoli salvati. Le decisioni restano annullabili dallo storico della scheda.`
    : "Nessuna azienda salvata. Nella tab Aziende usa «Salva azienda» o «Salva ruolo».";
  $("saved-items").replaceChildren();
  for (const company of data.items) {
    const row = el("article", undefined, "queue-company");
    const heading = el("div", undefined, "queue-company-heading");
    const title = el("div");
    title.append(el("h3", company.name), el("p", `${company.tier_label} · ${companyCategories(company).join(" · ")}`, "muted"));
    const open = el("button", "Apri qui", "primary");
    open.setAttribute("aria-label", "Apri " + company.name);
    open.addEventListener("click", guarded(() => openSaved(company.id)));
    heading.append(title, open);
    row.append(heading);
    if (company.roles.length) {
      const list = el("ul");
      for (const role of company.roles) {
        const item = el("li");
        const link = el("button", role.title, "company-link");
        link.addEventListener("click", guarded(() => openSaved(company.id, role.id)));
        item.append(link, el("span", " " + (role.verdetto ? verdictLabels[role.verdetto] || role.verdetto : "senza verdetto"), "muted"));
        list.append(item);
      }
      row.append(el("h4", `Ruoli salvati · ${company.roles.length}`), list);
    } else {
      row.append(el("p", "Azienda salvata senza un ruolo specifico.", "muted"));
    }
    const research = el("button", "Prepara testo per la chat");
    research.addEventListener("click", guarded(async () => {
      const brief = await api("/api/research/" + company.id);
      const text = [`Usa la skill jobhunter per approfondire ${brief.name} (ID ${company.id}).`,
                    "Verifica online i ruoli ancora aperti e la coerenza con il mio profilo. Distingui fatti verificati e informazioni mancanti.",
                    "", "Domande:", ...brief.questions.map(q => "- " + q),
                    "", "Link da verificare:", ...[brief.website, ...brief.opportunities.map(o => o.url)].filter(Boolean)].join("\n");
      row.append(copyBox("Testo da copiare nella chat di Codex", text));
      research.disabled = true;
    }));
    const actions = el("div", undefined, "queue-chat-actions");
    actions.append(research, el("small", "Prepara un testo. Non invia messaggi e non avvia un modello."));
    row.append(actions);
    $("saved-items").append(row);
  }
}

let savedItems = [];
const verdictLabels = {tieni: "compatibile", scarta: "scartato dalla pipeline", non_so: "da decidere"};

/** Un'area di testo pronta da copiare: la pagina non manda niente da nessuna parte. */
function copyBox(title, text) {
  const label = el("label", title);
  const area = el("textarea");
  area.value = text;
  area.rows = Math.min(20, text.split("\n").length + 1);
  area.readOnly = true;
  label.append(area);
  setTimeout(() => { area.focus(); area.select(); }, 0);
  return label;
}

/** Prepara un ingresso esplicito alla skill; la sessione persistente nasce poi dalla chat. */
function prepareCodexConversation(mode) {
  const text = mode === "indecisi"
    ? ["Usa la skill jobhunter e avvia o riprendi una revisione degli indecisi di Jev.",
       "Lavora in piccoli batch con `python main.py codex-session start --mode indecisi`.",
       "Escludi gli annunci senza descrizione, carica il testo integrale solo quando serve e fammi al massimo tre domande mirate per volta.",
       "Se emerge una regola generale, mostrane l'impatto prima di proporre modifiche alla pipeline. Salva come memoria solo ciò che confermo esplicitamente."].join("\n")
    : ["Usa la skill jobhunter e avvia un'esplorazione della selezione Tier A/B.",
       "Prima definisci con me paese, città, categoria o ricerca testuale; poi usa `python main.py codex-session start --mode selezione` con quei filtri.",
       "Confronta piccoli batch usando prima riassunti e metadati. Espandi i testi originali solo per i casi davvero utili.",
       "Le mie osservazioni non devono diventare filtri automatici: salva come memoria solo le preferenze che confermo esplicitamente."].join("\n");
  $("codex-prompt-box").replaceChildren(copyBox("Testo da copiare nella chat di Codex", text));
}

/** Apre la scheda dentro la tab Salvate, sul ruolo scelto quando ce n'è uno. */
async function openSaved(id, roleId) {
  await show(id, {panel: $("saved-detail"), filtered: false, refresh: loadSaved});
  const target = roleId ? $("saved-detail").querySelector("#job-" + roleId) : $("saved-detail");
  if (target instanceof HTMLDetailsElement) target.open = true;
  target?.scrollIntoView({block: "start"});
}

/** Un solo testo per tutte le salvate: il controllo periodico si fa in una volta. */
function savedBrief() {
  if (!savedItems.length) return;
  const lines = savedItems.flatMap(company => [
    `- ${company.name} (ID ${company.id})${company.website ? " · " + company.website : ""}`,
    ...(company.roles.length
      ? company.roles.map(role => `  - ${role.title}${role.url ? " " + role.url : ""}`)
      : ["  - nessun ruolo specifico salvato"])]);
  const text = [`Usa la skill jobhunter. Controlla le ${savedItems.length} aziende che ho salvato a mano.`,
                "Per ognuna: i ruoli elencati sono ancora aperti? Ci sono nuovi ruoli adatti al mio profilo? Cosa manca per decidere?",
                "Distingui i fatti verificati sulle fonti dalle informazioni mancanti, e non inventare requisiti.",
                "", ...lines].join("\n");
  $("saved-brief-box").replaceChildren(copyBox("Testo da copiare nella chat di Codex", text));
}

/** Stato del percorso Debug: ambito, controllo, sottogruppo e pagina restano visibili insieme. */
let debugCatalog = [], debugScope = "aziende", debugLens = null;
let debugRowsRequest = 0, debugDetailRequest = 0;

/** Etichetta italiana dell'ambito usata nei pulsanti e nel breadcrumb. */
function debugScopeLabel(value) {
  return value === "aziende" ? "Aziende" : "Annunci";
}

/** Disegna i primi due livelli del percorso senza nascondere i conteggi delle altre verifiche. */
function renderDebugNavigation() {
  const scopes = $("debug-scope");
  scopes.replaceChildren();
  for (const scope of ["aziende", "annunci"]) {
    const lenses = debugCatalog.filter(lens => lens.scope === scope);
    const count = lenses.reduce((sum, lens) => sum + lens.count, 0);
    const button = el("button", undefined, "debug-scope-button");
    button.append(el("strong", debugScopeLabel(scope)), el("span", `${nf(count)} occorrenze · ${lenses.length} controlli`));
    button.setAttribute("aria-pressed", String(scope === debugScope));
    button.addEventListener("click", guarded(async () => {
      debugScope = scope;
      debugLens = null;
      renderDebugNavigation();
      await showDebugRows(lenses[0], "");
    }));
    scopes.append(button);
  }

  const navigation = $("debug-lenses");
  navigation.replaceChildren();
  const sections = new Map();
  for (const lens of debugCatalog.filter(item => item.scope === debugScope)) {
    if (!sections.has(lens.section)) sections.set(lens.section, []);
    sections.get(lens.section).push(lens);
  }
  for (const [section, lenses] of sections) {
    const group = el("section", undefined, "debug-section");
    group.append(el("h3", section));
    for (const lens of lenses) {
      const button = el("button", undefined, "debug-lens-open");
      button.append(el("span", lens.label), el("strong", `${nf(lens.count)} ${lens.unit}`));
      button.setAttribute("aria-pressed", String(debugLens?.lens.id === lens.id));
      button.addEventListener("click", guarded(() => showDebugRows(lens, "")));
      group.append(button);
    }
    navigation.append(group);
  }
}

async function loadDebug() {
  $("debug-status").textContent = "Conteggio in corso…";
  const data = await api("/api/debug");
  debugCatalog = data.lenses;
  if (!debugCatalog.some(lens => lens.scope === debugScope)) debugScope = debugCatalog[0]?.scope || "aziende";
  const current = debugLens && debugCatalog.find(lens => lens.id === debugLens.lens.id);
  if (current) debugLens.lens = current;
  renderDebugNavigation();
  $("debug-status").textContent = "Conteggi aggiornati alle " + new Date().toLocaleTimeString("it-IT");
  const lens = current || debugCatalog.find(item => item.scope === debugScope);
  if (lens) await showDebugRows(lens, current ? debugLens.value : "", current ? debugLens.offset : 0,
                                current ? debugLens.facetSet : false);
}

/** Mostra il record selezionato in sola lettura, lasciando elenco, filtro e percorso al loro posto. */
async function showDebugDetail(item, lens) {
  const request = ++debugDetailRequest;
  const panel = $("debug-detail");
  panel.replaceChildren(el("p", "Caricamento dettaglio…", "muted"));
  const company = await api("/api/company/" + item.company_id);
  if (request !== debugDetailRequest) return;
  panel.replaceChildren();
  const heading = el("div", undefined, "detail-heading");
  heading.append(el("h3", company.name), el("span", company.tier || "Senza tier", "tag"));
  panel.append(heading, el("p", lens.label, "muted"));
  panel.append(factList([
    ["Categorie", companyCategories(company).join(" · ")],
    ["Stato", labels[company.status] || company.status],
    ["Sito", company.website ? link(company.website, hostname(company.website)) : "Non disponibile"],
  ]));
  panel.append(axisSummary(company));

  const opportunity = company.opportunities.find(job => job.id === item.id);
  if (opportunity) {
    panel.append(el("h4", opportunity.title));
    panel.append(el("p", [locationPreview(opportunity.locations), date(opportunity.last_seen_at)].join(" · "), "muted"));
    if (opportunity.verdict?.verdetto) panel.append(roleVerdict(opportunity.verdict));
    const sources = el("p", undefined, "job-links");
    sources.append(link(opportunity.application_url, "Apri annuncio"));
    for (const source of opportunity.sources) sources.append(link(source.source_url, source.source + " · " + date(source.observed_at)));
    panel.append(sources);
    const proof = el("details", undefined, "debug-evidence");
    proof.append(el("summary", "Evidenze del controllo"));
    if (opportunity.description_check) proof.append(el("p", "Recupero descrizione: " + opportunity.description_check.status));
    for (const quote of opportunity.selection?.requirements?.evidence || []) proof.append(el("blockquote", quote));
    if (!proof.querySelector("p, blockquote")) proof.append(el("p", "Nessuna evidenza aggiuntiva registrata."));
    panel.append(proof);
  } else {
    panel.append(el("p", company.remote_summary?.summary || company.description || "Descrizione aziendale non disponibile."));
    const evidence = el("details", undefined, "debug-evidence");
    evidence.append(el("summary", `Evidenze aziendali · ${company.evidence.length}`));
    for (const record of company.evidence) {
      const line = el("p", record.note || record.kind || "Evidenza registrata");
      if (record.url) line.append(el("span", " "), link(record.url, "fonte"));
      evidence.append(line);
    }
    if (!company.evidence.length) evidence.append(el("p", "Nessuna evidenza aziendale registrata."));
    panel.append(evidence);
  }

  const open = el("button", "Apri la scheda completa in Aziende", "primary");
  open.addEventListener("click", guarded(async () => {
    document.querySelector('[data-view="companies"]').click();
    await show(item.company_id);
    $("detail").scrollIntoView({block: "start"});
  }));
  panel.append(open);
  if (matchMedia("(max-width: 720px)").matches) {
    panel.focus({preventScroll: true});
    panel.scrollIntoView({block: "start"});
  }
}

/** Una pagina di risultati con sottogruppo esplicito, accesso alla fonte e dettaglio contestuale. */
async function showDebugRows(lens, value, offset = 0, facetSet = false) {
  const request = ++debugRowsRequest;
  ++debugDetailRequest;
  debugScope = lens.scope;
  debugLens = {lens, value, offset, facetSet};
  renderDebugNavigation();
  $("debug-detail").replaceChildren(el("h3", "Scegli un risultato"), el("p", "Il dettaglio resta accanto all'elenco, così non perdi il percorso del controllo."));
  const data = await api("/api/debug?" + new URLSearchParams({
    lens: lens.id, value, offset, limit: 50, facet_set: facetSet ? "1" : "0",
  }));
  if (request !== debugRowsRequest) return;
  const subgroup = facetSet ? value || "Senza valore" : "";
  $("debug-path").textContent = ["Debug", debugScopeLabel(lens.scope), lens.section, lens.label, subgroup].filter(Boolean).join(" / ");
  const area = $("debug-rows");
  area.replaceChildren();
  const heading = el("div", undefined, "debug-results-heading");
  heading.append(el("h3", data.label), el("strong", `${nf(data.total)} ${data.unit}`));
  area.append(heading);
  if (lens.facets.length) {
    const label = el("label", lens.facet_label || "Sottogruppo", "debug-filter");
    const select = el("select");
    const all = el("option", `Tutti · ${nf(lens.count)}`);
    all.value = "__all__";
    select.append(all);
    for (const facet of lens.facets) {
      const option = el("option", `${facet.value || "Senza valore"} · ${nf(facet.count)}`);
      option.value = facet.value;
      select.append(option);
    }
    select.value = facetSet ? value : "__all__";
    select.addEventListener("change", guarded(() => {
      const selected = select.value !== "__all__";
      return showDebugRows(lens, selected ? select.value : "", 0, selected);
    }));
    label.append(select);
    area.append(label);
  }
  const start = data.total ? offset + 1 : 0;
  area.append(el("p", `${start}–${Math.min(offset + data.items.length, data.total)} di ${nf(data.total)}`, "muted"));
  const list = el("ol", undefined, "debug-list");
  list.start = start || 1;
  for (const item of data.items) {
    const row = el("li");
    const open = el("button", item.name, "debug-result-open");
    open.setAttribute("aria-controls", "debug-detail");
    open.addEventListener("click", guarded(async () => {
      for (const button of list.querySelectorAll(".debug-result-open")) button.removeAttribute("aria-current");
      open.setAttribute("aria-current", "true");
      await showDebugDetail(item, lens);
    }));
    row.append(open);
    if (item.detail) row.append(el("span", item.detail, "debug-result-detail"));
    if (item.facet && !value) row.append(el("span", item.facet, "tag"));
    if (item.url) row.append(link(item.url, "Apri fonte"));
    list.append(row);
  }
  if (!data.items.length) list.append(el("li", "Nessun caso in questo sottogruppo."));
  area.append(list);
  const pager = el("div", undefined, "pager");
  const page = el("span", `Pagina ${data.total ? Math.floor(offset / 50) + 1 : 0} di ${Math.ceil(data.total / 50)}`);
  const previous = el("button", "Precedenti");
  previous.disabled = offset === 0;
  previous.addEventListener("click", guarded(() => showDebugRows(lens, value, Math.max(0, offset - 50), facetSet)));
  const next = el("button", "Successive");
  next.disabled = offset + 50 >= data.total;
  next.addEventListener("click", guarded(() => showDebugRows(lens, value, offset + 50, facetSet)));
  pager.append(previous, page, next);
  area.append(pager);
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
        for (const name of ["companies", "sources", "saved", "analytics", "pipeline", "debug"])
          $(name + "-view").hidden = name !== button.dataset.view;
        if (button.dataset.view === "sources") await sources();
        if (button.dataset.view === "saved") await loadSaved();
        if (button.dataset.view === "analytics") await loadAnalytics();
        if (button.dataset.view === "pipeline") await loadPipeline();
        if (button.dataset.view === "debug") await loadDebug();
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
      refillCities();
      // L'ordinamento non è un filtro: Azzera pulisce la ricerca, non il modo di guardarla.
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
  $("sort").value = remembered("sort") || "recenti";
  $("sort").addEventListener("change", guarded(async () => {
    remember("sort", $("sort").value);
    offset = 0;
    await load();
  }));
  setupColumns();
  setupResize();
  $("export").addEventListener("click", guarded(exportCSV));
  $("refresh-saved").addEventListener("click", guarded(loadSaved));
  $("refresh-debug").addEventListener("click", guarded(loadDebug));
  $("saved-brief").addEventListener("click", guarded(savedBrief));
  $("refresh-analytics").addEventListener("click", guarded(loadAnalytics));
  $("refresh-pipeline").addEventListener("click", guarded(loadPipeline));
  $("codex-review-prompt").addEventListener("click", () => prepareCodexConversation("indecisi"));
  $("codex-selection-prompt").addEventListener("click", () => prepareCodexConversation("selezione"));
  $('pipeline-close').addEventListener('click', () => $('pipeline-dialog').close());
  $('pipeline-form').addEventListener('submit', launchPipeline);
  $('pipeline-stop').addEventListener('click', () => stopPipeline(pipelineData?.controls.active?.id));
  $("analytics-scope").addEventListener("change", guarded(loadAnalytics));
  await load();
  // I risultati non aspettano i menu geografici: arrivano quando sono pronti.
  setupPlaces().catch((error) => message(error.message, true));
}
/** Il bottone del tema funziona anche se il server non risponde: non aspetta il bootstrap. */
function setupTheme() {
  const root = document.documentElement, toggle = $("theme-toggle");
  const sync = () => {
    const text = root.dataset.theme === "dark" ? "Passa al tema chiaro" : "Passa al tema scuro";
    toggle.setAttribute("aria-label", text);
    toggle.title = text;
  };
  toggle.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    remember("theme", root.dataset.theme);
    sync();
  });
  // Finché non scegli a mano, il tema segue il sistema anche a pagina aperta.
  darkQuery.addEventListener("change", (event) => {
    if (remembered("theme")) return;
    root.dataset.theme = event.matches ? "dark" : "light";
    sync();
  });
  sync();
}
// Lo script non è differito (serve al tema), quindi la pagina parte quando il documento è pronto.
document.addEventListener("DOMContentLoaded", () => {
  setupTheme();
  init().then(() => {
    if (new URLSearchParams(location.search).get('view') === 'pipeline') document.querySelector('[data-view="pipeline"]').click();
  }).catch((error) => message(error.message, true));
});

/** Render comparable counts with a shared denominator and no chart dependency. */
const categoryMethods = {rules: "da parole chiave", jev: "da Jev",
  chat: "scelta tua", unknown: "nessuna corrispondenza"};

/** Normalize the transitional first-category field into the multi-label UI contract. */
function companyCategories(company) {
  return company.categories?.length ? company.categories : [company.category || "Da classificare"];
}

const nf = (value) => Number(value || 0).toLocaleString("it-IT");
const pf = (value, total) => (total > 0 ? (value * 100) / total : 0).toLocaleString("it-IT", {maximumFractionDigits: 1}) + "%";
const SVG = "http://www.w3.org/2000/svg";
/** Un nodo SVG con soli attributi geometrici: la CSP blocca gli stili inline, non questi. */
function svgNode(tag, attributes, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Una quota disegnata contro il suo totale, mai contro il massimo della lista: barre confrontabili fra righe. */
function meter(label, value, total, color = "seg-in") {
  const row = el("div", undefined, "meter");
  const head = el("div", undefined, "meter-head");
  head.append(el("span", label, "meter-label"), el("span", `${nf(value)} · ${pf(value, total)}`, "meter-value"));
  const chart = svgNode("svg", {viewBox: "0 0 1000 10", preserveAspectRatio: "none", class: "funnel-chart meter-bar", "aria-hidden": "true"});
  chart.append(svgNode("rect", {x: 0, y: 0, width: total > 0 ? (value / total) * 1000 : 0, height: 10, class: "seg " + color}));
  row.append(head, chart);
  return row;
}

/** Una scheda di metriche: titolo, denominatore dichiarato una volta sola, poi le quote disegnate da `draw`. */
function metricCard(title, total, unit, rows, hint, draw = meters) {
  const section = el("section", undefined, "metric-section");
  const heading = el("div", undefined, "share-heading");
  heading.append(el("strong", title), el("span", `su ${nf(total)} ${unit}`));
  section.append(heading);
  if (hint) section.append(el("p", hint, "hint"));
  section.append(...draw(rows, total));
  return section;
}
const meters = (rows, total) => rows.map(([label, value, color]) => meter(label, value, total, color));

/** Uno spicchio di corona circolare da a0 ad a1 (radianti, 0 in alto, senso orario). */
function ringArc(a0, a1, R = 50, r = 33) {
  // Un arco con inizio e fine coincidenti non si disegna: l'anello pieno si ferma a un soffio dal giro,
  // e lo scarto deve sopravvivere all'arrotondamento delle coordinate.
  a1 = Math.min(a1, a0 + 2 * Math.PI - 1e-3);
  const p = (radius, a) => `${(radius * Math.sin(a)).toFixed(3)} ${(-radius * Math.cos(a)).toFixed(3)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${p(R, a0)}A${R} ${R} 0 ${large} 1 ${p(R, a1)}L${p(r, a1)}A${r} ${r} 0 ${large} 0 ${p(r, a0)}Z`;
}

/** Una ciambella: spicchi contro il totale, la parte non coperta resta binario vuoto. Ogni spicchio ha il suo tooltip. */
function donut(rows, total, center) {
  const chart = svgNode("svg", {viewBox: "-51 -51 102 102", class: "donut", "aria-hidden": "true"});
  chart.append(svgNode("path", {d: ringArc(0, 2 * Math.PI), class: "track"}));
  let a = 0;
  for (const [label, value, color] of rows) {
    const b = a + (total > 0 ? value / total : 0) * 2 * Math.PI;
    if (b - a > 1e-3) {
      const seg = svgNode("path", {d: ringArc(a, b), class: "seg " + color});
      seg.append(svgNode("title", {}, `${label}: ${nf(value)} · ${pf(value, total)}`));
      chart.append(seg);
    }
    a = b;
  }
  chart.append(svgNode("text", {x: 0, y: 5, "text-anchor": "middle", class: "donut-center"}, center));
  return chart;
}

/** Parte-tutto: la ciambella per il colpo d'occhio, la legenda per i numeri esatti. */
function donutWithLegend(rows, total) {
  const body = el("div", undefined, "donut-body");
  const legend = el("ul", undefined, "funnel-legend donut-legend");
  for (const [label, value, color] of rows) {
    const item = el("li");
    const swatch = el("span", undefined, "swatch " + color);
    swatch.setAttribute("aria-hidden", "true");
    item.append(swatch, el("span", label), el("strong", `${nf(value)} · ${pf(value, total)}`, "count"));
    legend.append(item);
  }
  body.append(donut(rows, total, nf(total)), legend);
  return [body];
}

/** Quote indipendenti (non sommano al totale): una piccola ciambella per ciascuna, mai spicchi della stessa. */
function gauges(rows, total) {
  const grid = el("div", undefined, "gauge-grid");
  for (const row of rows) {
    const cell = el("figure", undefined, "gauge");
    cell.append(donut([row], total, pf(row[1], total)), el("figcaption", `${row[0]} · ${nf(row[1])}`));
    grid.append(cell);
  }
  return [grid];
}

/** Una ciambella regge al massimo sei spicchi: cinque categorie con un colore ciascuno, il resto in grigio.
Chi non ha categoria va in fondo e resta binario vuoto, come il dato mancante negli anelli di completezza. */
function categorySlices(categories) {
  const known = categories.filter(row => row.label !== "Da classificare");
  const slices = ranked(known, 5, "Altre categorie").map(([label, value, color], index) =>
    [label, value, color === "seg-pending" ? "seg-pending" : "cat-" + (index + 1)]);
  const unclassified = categories.find(row => row.label === "Da classificare");
  if (unclassified) slices.push(["Da classificare", unclassified.count, "track"]);
  return slices;
}

/** Le prime voci di una distribuzione lunga, con la coda raccolta in una riga sola invece che troncata. */
function ranked(rows, limit, restLabel = "Altre voci") {
  const head = rows.slice(0, limit).map((row, index) => [row.label, row.count, index === 0 ? "seg-in" : "seg-rank"]);
  const rest = rows.slice(limit).reduce((sum, row) => sum + row.count, 0);
  if (rest) head.push([`${restLabel} (${rows.length - limit})`, rest, "seg-pending"]);
  return head;
}

let analyticsRequest = 0;
/** Refresh read-only distributions; stale responses cannot replace a newer scope. */
async function loadAnalytics() {
  const request = ++analyticsRequest;
  $("analytics-status").textContent = "Calcolo delle metriche in corso…";
  $("analytics-content").replaceChildren();
  try {
    // Il percorso vive nel monitor della pipeline e riguarda sempre tutto l'archivio: le due letture
    // sono indipendenti, e un funnel non disponibile non deve togliere le distribuzioni.
    const [data, pipeline] = await Promise.all([
      api("/api/analytics?" + new URLSearchParams({eligibility: $("analytics-scope").value})),
      api("/api/pipeline").catch(() => null)]);
    if (request !== analyticsRequest) return;
    $("analytics-status").textContent = `${nf(data.total)} annunci · ${nf(data.companies)} aziende · Aggiornato alle ${new Date(data.generated_at).toLocaleTimeString("it-IT")}`;
    const content = $("analytics-content");
    renderJourney(content, pipeline?.funnel);
    if (!data.total) { content.append(el("p", "Nessun annuncio in questa selezione.")); return; }

    const scoped = el("section", undefined, "metrics-block");
    scoped.append(el("h3", "Qualità e composizione della selezione"));
    scoped.append(el("p", $("analytics-scope").value
      ? "Questa sezione segue il filtro qui sopra; il percorso resta sull'intero archivio."
      : "Conteggi su annunci univoci. La categoria è il settore dell'azienda, non la mansione. Un campo presente non ne garantisce correttezza o attualità.", "hint"));
    const selection = Object.fromEntries(data.selection.map(row => [row.label, row.count]));
    const grid = el("div", undefined, "metrics-grid");
    grid.append(
      metricCard("Completezza dei dati", data.total, "annunci", [
        ["Con descrizione completa", data.health.with_description, "seg-keep"],
        ["Con categoria aziendale", data.health.categorized, "seg-keep"],
        ["Con paese riconosciuto", data.health.country_known, "seg-in"],
        ["Con salario dichiarato", data.health.with_salary, "seg-review"],
        ["Con data di pubblicazione", data.health.with_posted_date, "seg-review"]],
        "Ogni anello è la parte presente: il tratto vuoto è il dato mancante.", gauges),
      metricCard("Esito dei filtri locali", data.total, "annunci", [
        ["Potenzialmente compatibili", selection.potential || 0, "seg-keep"],
        ["Da verificare", selection.review || 0, "seg-review"],
        ["Esclusi dai filtri locali", selection.excluded || 0, "seg-gone"]],
        "Le tre quote sommano al totale: è la tappa 3 del percorso, ristretta a questa selezione.", donutWithLegend),
      metricCard("Categorie aziendali", data.total, "annunci", categorySlices(data.categories),
        "Categoria dell'azienda che pubblica, contata una volta per annuncio.", donutWithLegend));
    scoped.append(grid);

    const geography = el("div", undefined, "metrics-grid");
    geography.append(
      metricCard("Continenti", data.total, "annunci", ranked(data.continents, 6, "Altri continenti")),
      metricCard("Paesi", data.total, "annunci", ranked(data.countries, 10, "Altri paesi")));
    scoped.append(el("h3", "Distribuzione geografica"),
      el("p", "Un annuncio può contare per più paesi e continenti: le percentuali possono superare il 100%. Si riconoscono nomi e codici espliciti; città isolate e sigle ambigue restano non determinate. Remoto non significa disponibile ovunque.", "hint"),
      geography);
    content.append(scoped);
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
const aboutOpen = new Set();
/** Cosa fa ogni passaggio, in breve: su cosa lavora e se classifica, scarta o solo prepara. */
const pipelineAbout = {
  collection: ['Annunci', 'Scarica gli annunci dalle fonti configurate e li salva in archivio. Non classifica e non scarta nulla.'],
  normalization: ['Annunci → aziende', 'Automatico a ogni importazione: uniforma i campi e raggruppa gli annunci sotto la loro azienda. Non classifica e non scarta.'],
  descriptions: ['Aziende, tramite i loro annunci', 'Scarica il testo completo degli annunci, a partire da uno per ogni azienda ancora senza evidenza. Non giudica: prepara il materiale per i giudici.'],
  filters: ['Annunci', 'Regole locali su titolo e descrizione, gratis. Classifica ogni annuncio: compatibile, escluso o «non so». Marca, non elimina: gli esclusi restano in archivio.'],
  jev: ['Annunci e aziende', 'Una richiesta può decidere il ruolo e assegnare il settore dell’azienda. Jev risponde a domande indipendenti; il codice applica le soglie e salva i risultati separatamente. Non sovrascrive una categoria scelta in chat.'],
  remote: ['Aziende e annunci', 'Una chiamata a Qwen per azienda, a pagamento. È l’unico passaggio che scrive, e l’unico che non giudica: riassume gli annunci sopravvissuti di Tier A e B e compone la scheda dell’azienda. Verdetti e categorie arrivano già decisi dai passaggi precedenti.'],
  queue: ['Aziende', 'Incrocia asse ruolo e asse azienda nel Tier e mette in coda le aziende di Tier A e B. Non scarta: ordina e propone.'],
  feedback: ['Aziende e annunci', 'Le tue decisioni, su un’azienda intera o su un singolo ruolo. È l’unico passaggio che decide in modo definitivo.']};

/** Read pipeline evidence without starting collection, filtering or model work. */
async function loadPipeline() {
  const button = $('refresh-pipeline');
  if (button.disabled) return;
  button.disabled = true;
  $('pipeline-status').textContent = 'Lettura dello stato della pipeline…';
  try {
    const data = await api('/api/pipeline');
    pipelineData = data;
    $('pipeline-status').textContent = `Archivio: ${data.opportunities.toLocaleString('it-IT')} annunci · ${data.companies.toLocaleString('it-IT')} aziende · Lettura delle ${new Date(data.generated_at).toLocaleTimeString('it-IT')}`;
    const workflow = $('pipeline-workflow');
    workflow.replaceChildren(el('h3', 'Ultimo workflow esterno registrato'));
    workflow.append(el('p', 'Questa cronologia non indica un comando attivo nella web app.', 'muted'));
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
      const about = pipelineAbout[step.id];
      if (about) {
        // Fuori dalla card: un bottone non puo' contenere un altro controllo.
        // Le card si ridisegnano a ogni aggiornamento: senza memoria il riquadro si richiuderebbe da solo.
        const info = el('details', undefined, 'pipeline-about');
        info.open = aboutOpen.has(step.id);
        info.addEventListener('toggle', () => info.open ? aboutOpen.add(step.id) : aboutOpen.delete(step.id));
        info.append(el('summary', 'Cosa fa'), el('p', `Lavora su: ${about[0]}. ${about[1]}`));
        row.append(info);
      }
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
    renderPipelineActivity();
    renderPipelineHandoff(data.funnel?.handoff);
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

/** Summarize current archive work without promising which records a future paid run will submit. */
function renderPipelineHandoff(handoff) {
  const area = $('pipeline-handoff');
  const heading = $('pipeline-handoff-title');
  area.replaceChildren(heading);
  if (!handoff) {
    area.append(el('p', 'Riepilogo operativo non disponibile.', 'hint'));
    return;
  }
  const counts = handoff.counts || {};
  const facts = el('dl', undefined, 'pipeline-handoff-facts');
  for (const [label, value, note] of [
    ['Candidati a Jev', counts.jev_ready, 'Il comando applicherà ancora limiti e vincoli del payload.'],
    ['Indecisi dopo Jev', counts.jev_review, 'Nessun giudice automatico viene dopo: restano a te.'],
    ['Bloccati dai dati', counts.blocked, 'Serve una descrizione utilizzabile prima del giudizio semantico.'],
    ['Da aggiornare o verificare', counts.stale, 'Testo, regole, profilo o configurazione non coincidono più.']]) {
    facts.append(el('dt', label));
    const detail = el('dd');
    detail.append(el('strong', nf(value || 0)), el('small', note));
    facts.append(detail);
  }
  area.append(facts, el('p', 'Sono conteggi dell’archivio corrente, non chiamate API garantite.', 'hint'));
}

/** Render a proportional bar with a single labelled count for every share, including zeroes. */
function pipelineShares(title, total, parts, legend = true) {
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
  if (!legend) return section;
  const list = el('ul', undefined, 'funnel-legend');
  for (const [color, label, value] of parts) {
    const item = el('li');
    const swatch = el('span', undefined, 'swatch ' + color);
    swatch.setAttribute('aria-hidden', 'true');
    item.append(swatch, el('span', label), el('strong', n(value), 'count'));
    list.append(item);
  }
  section.append(list);
  return section;
}

/** Keep coverage and its population together, without repeating the card's status.

Ogni card apre dichiarando quanti ne ha ricevuti e da quale passaggio. Senza quella riga un totale
nato da una sottrazione, o un cambio di unita' fra annunci e aziende, resta un numero che compare
dal nulla: e' esattamente il punto in cui il lettore perde il filo fra una card e la successiva.
*/
function stepMeasure(step) {
  const box = el('div', undefined, 'pipeline-measure');
  if (step.inflow) box.append(el('p', step.inflow, 'pipeline-inflow'));
  if (step.total !== null && step.total > 0) {
    // Un passaggio che si ferma per informazione mancante ha tre quote, non due: le dichiara lui.
    // La barra usa `base`: dove una quota non e' lavorabile, il totale disegnato non e' il denominatore
    // della copertura, e sommare le quote contro quest'ultimo le disegnerebbe piu' larghe del vero.
    box.append(pipelineShares(step.measure || '', step.base ?? step.total, step.parts || [
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

/** Il percorso come sequenza di barre tutte larghe uguali: il denominatore non cambia mai da una tappa
all'altra, percio' due quote di tappe diverse si confrontano a occhio senza rifare il conto.

La fascia scura a sinistra e' chi e' gia' uscito nelle tappe precedenti: cresce verso destra riga dopo
riga, e quel bordo che scivola e' il funnel. Il colore non porta mai da solo un'informazione: ogni
numero e' scritto nella barra quando ci sta, e comunque nella legenda della riga.
*/
function renderJourney(area, funnel) {
  const stages = funnel?.percorso;
  if (!stages?.length) {
    area.append(el('p', 'Percorso non disponibile. Riavvia il server al termine dell’esecuzione.', 'hint'));
    return;
  }
  const block = el('section', undefined, 'metrics-block journey');
  block.append(el('h3', 'Il percorso degli annunci'));
  block.append(el('p', 'Il percorso riguarda sempre tutto l’archivio, anche quando il filtro qui sopra è attivo. Ogni barra dichiara la propria unità; l’ultima passa dagli annunci alle aziende.', 'hint'));
  if (funnel.handoff) {
    const handoff = el('section', undefined, 'journey-handoff');
    handoff.append(el('h4', 'Cosa arriva, si ferma o è già deciso prima di una nuova chiamata'));
    handoff.append(pipelineShares('Partizione corrente · annunci', funnel.handoff.base, funnel.handoff.parts));
    handoff.append(el('p', funnel.handoff.note, 'hint'));
    block.append(handoff);
  }
  const key = el('ul', undefined, 'funnel-legend journey-key');
  for (const [color, label] of [['seg-gone', 'Usciti'], ['seg-keep', 'Compatibili'], ['seg-in', 'In gioco'],
                               ['seg-review', 'Ancora da decidere'], ['seg-pending', 'Fermi: manca un’informazione']]) {
    const item = el('li');
    const swatch = el('span', undefined, 'swatch ' + color);
    swatch.setAttribute('aria-hidden', 'true');
    item.append(swatch, el('span', label));
    key.append(item);
  }
  block.append(key);

  const W = 1000, BAR = 42, ROW = 82, TOP = 26;
  const chart = svgNode('svg', {viewBox: `0 0 ${W} ${TOP + stages.length * ROW}`, class: 'funnel-chart journey-chart', role: 'img'});
  let previous = null;
  for (const [index, stage] of stages.entries()) {
    const base = stage.base || 0;
    const label = TOP + index * ROW, top = label + 10;
    chart.append(svgNode('text', {x: 0, y: label, class: 'stage-label'}, stage.title));
    chart.append(svgNode('text', {x: W, y: label, class: 'stage-total', 'text-anchor': 'end'}, `${nf(base)} ${stage.unit}`));
    chart.append(svgNode('rect', {x: 0, y: top, width: W, height: BAR, rx: 5, class: 'track'}));
    let x = 0, gone = 0;
    for (const [color, text, value] of stage.parts) {
      const width = base > 0 ? (value / base) * W : 0;
      if (width >= 0.5) {
        // Il tooltip dice solo la quota sotto il puntatore: il riepilogo intero sta nel riquadro espandibile.
        const seg = svgNode('rect', {x, y: top, width, height: BAR, class: 'seg ' + color});
        seg.append(svgNode('title', {}, `${stage.title} · ${text}: ${nf(value)} ${stage.unit} (${pf(value, base)})`));
        chart.append(seg);
        const middle = x + width / 2;
        if (width >= 46) chart.append(svgNode('text', {x: middle, y: top + (width >= 84 ? 19 : BAR / 2 + 5), class: 'seg-label', 'text-anchor': 'middle'}, nf(value)));
        if (width >= 84) chart.append(svgNode('text', {x: middle, y: top + 33, class: 'seg-share', 'text-anchor': 'middle'}, pf(value, base)));
      }
      x += width;
      if (color === 'seg-gone') gone = x;
    }
    // Il bordo di chi e' uscito scivola verso destra: la riga che segue eredita il taglio della
    // precedente. Dove il taglio non c'era ancora non si disegna nulla: una diagonale lunga tutta la
    // pagina direbbe solo che la tappa prima non escludeva nessuno, e lo dice gia' la barra piena.
    if (gone > 0 && previous > 0 && !stage.unit_change)
      chart.append(svgNode('line', {x1: previous, y1: top - ROW + BAR, x2: gone, y2: top, class: 'gone-edge'}));
    previous = gone;
  }
  chart.setAttribute('aria-label', stages.map(stage =>
    `${stage.title}: ${nf(stage.base)} ${stage.unit}, di cui ` +
    stage.parts.map(([, text, value]) => `${nf(value)} ${text}`).join(', ')).join('. '));
  const scroll = el('div', undefined, 'journey-scroll');
  scroll.append(chart);
  block.append(scroll);

  const detail = el('details', undefined, 'journey-detail');
  detail.append(el('summary', 'I numeri di ogni tappa, con le etichette per esteso'));
  for (const stage of stages) {
    detail.append(pipelineShares(`${stage.title} · ${stage.unit}`, stage.base, stage.parts));
    if (stage.note) detail.append(el('p', stage.note, 'muted'));
  }
  if (funnel.basis) detail.append(el('p', funnel.basis, 'muted'));
  block.append(detail);

  const axes = el('section', undefined, 'metrics-block');
  axes.append(el('h3', 'Due assi indipendenti → Tier'),
    el('p', 'Ruolo e azienda si giudicano separatamente; il tier nasce dal loro incrocio e si ricalcola a ogni lettura.', 'hint'));
  const axesGrid = el('div', undefined, 'metrics-grid');
  axesGrid.append(
    axisCard('Asse ruolo · annunci', funnel.archive.jobs, [
      ['seg-keep', 'Compatibili', funnel.ruolo.tieni],
      ['seg-review', 'Da decidere', funnel.ruolo.non_so],
      ['seg-gone', 'Scartati', funnel.ruolo.scarta]]),
    axisCard('Asse azienda · aziende', funnel.archive.companies, [
      ['seg-keep', 'Interessanti', funnel.azienda.interessante],
      ['seg-pending', 'Evidenza mancante', funnel.azienda.evidenza_mancante],
      ['seg-gone', 'Fuori preferenze', funnel.azienda.non_interessante]]),
    axisCard('Tier dagli esiti salvati · aziende', funnel.archive.companies, [
      ['seg-keep', 'A · azienda e ruolo sì', funnel.tier.A],
      ['seg-in', 'B · attesa di un ruolo', funnel.tier['B-attesa']],
      ['seg-review', 'B · solo esperienza', funnel.tier['B-esperienza']],
      ['seg-pending', 'Evidenza mancante', funnel.tier['evidenza-mancante']],
      ['seg-gone', 'Scarto', funnel.tier.scarto]]),
    axisCard('Origine dei giudizi · annunci', funnel.archive.jobs, [
      ['seg-in', 'Regex', funnel.giudici?.regex || 0],
      ['seg-review', 'Jev', funnel.giudici?.jev || 0],
      ['seg-pending', 'Senza giudizio', funnel.giudici?.nessuno || 0]]));
  axes.append(axesGrid);
  area.append(block, axes);
}

/** Una partizione completa: la barra impilata per il colpo d'occhio, i metri sotto per i confronti fini. */
function axisCard(title, total, parts) {
  const section = el('section', undefined, 'metric-section');
  section.append(pipelineShares(title, total, parts, false));
  for (const [color, label, value] of parts) section.append(meter(label, value, total, color));
  return section;
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
      ['Schede dei ruoli', modern ? `${n(c.saved_summaries)} / ${n(c.summary_requests)}` : b ? `${n(b.validated_jobs)} / ${n(b.submitted_jobs)}` : '—',
        modern || b ? 'schede salvate su ruoli inviati' : 'non registrato dalla vecchia versione'],
      ['Schede aziendali', modern ? `${n(c.saved_company_cards)} / ${n(c.company_requests)}` : '—',
        'una per azienda di Tier A o B, indipendente dai ruoli'],
      ['Riusate dalla cache', modern ? n(c.cached_summaries) : n(c.cached), 'schede già valide: nessuna nuova chiamata'],
      ['Chiamate API', modern ? `${n(c.api_calls)} per ${n(c.api_companies)} aziende` : b ? n(b.calls) : '—',
        modern && c.rejected_companies ? `${n(c.rejected_companies)} con errore o risposta rifiutata` : 'una per azienda'],
      ['Token consumati', n(u.total_tokens), `${n(u.prompt_tokens)} input + ${n(u.completion_tokens)} output`]];
    // Le esecuzioni salvate prima del 20 settembre 2026 portano ancora i verdetti: vanno mostrate
    // come sono state, non riscritte con i conteggi di oggi.
    if (c.evaluated_jobs !== undefined) rows.splice(1, 0,
      ['Annunci valutati', `${n(c.evaluated_jobs)} / ${n(c.submitted_jobs)}`, 'quando questo passaggio giudicava ancora'],
      ['Esiti di allora', `${n(c.kept_jobs)} · ${n(c.review_jobs)} · ${n(c.remote_excluded)}`, 'da tenere · da verificare · esclusioni proposte']);
    if (c.unreadable_jobs) rows.push(['Senza mansioni leggibili', n(c.unreadable_jobs), 'niente da riassumere: mai inviati']);
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
  // La barra dei verdetti resta solo per le esecuzioni che i verdetti li producevano davvero.
  if (b || c.evaluated_jobs !== undefined) {
    const outcomes = [['seg-keep', 'Da tenere', b ? b.keep : c.kept_jobs],
      ['seg-review', 'Da verificare', b ? b.review : c.review_jobs],
      ['seg-exclude', 'Esclusioni proposte', b ? b.exclude : c.remote_excluded]];
    panel.append(pipelineShares('Esiti di allora · annunci', outcomes.reduce((sum, part) => sum + (part[2] || 0), 0), outcomes));
    rows = rows.filter(([label]) => label !== 'Esiti di allora');
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
  area.replaceChildren(el('h3', 'Attività della web app'));
  if (active) {
    const detail = active.detail;
    area.append(el('strong', 'In esecuzione: ' + (pipelineData.controls.actions[detail.phase || active.step]?.label || 'Sequenza')));

    if (progressOf(detail)) area.append(pipelineProgress(detail, active.started_at));

    area.append(el('small', 'Avviato ' + pipelineDate(active.started_at) + '. Mantieni attivo il server della web app.'));
    if (!pipelineData.controls.supports_stop) area.append(el('p', 'Il comando Interrompi richiede il riavvio del server dopo questa esecuzione.'));
  } else if (pipelineData.collection_running) area.append(el('p', 'Raccolta avviata dalla pagina Fonti in corso. Attendi prima di avviare un altro passaggio.'));
  else {
    area.append(el('p', 'Nessun comando attivo. Seleziona una card per iniziare; le esecuzioni concluse restano nella cronologia qui sotto.'));
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
    if (detail.mode === 'preview') container.append(el('p', `Anteprima: ${detail.counts?.planned_api_calls || 0} chiamate aziendali previste per ${detail.counts?.planned_summaries || 0} schede di ruolo e ${detail.counts?.planned_company_cards || 0} schede aziendali. Nessuna chiamata API effettuata, nessun costo.`));
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
  const labels = {source: 'Fonte', limit: step === 'jev' ? 'Numero massimo di richieste Jev' : 'Numero massimo di annunci', workers: step === 'remote' ? 'Chiamate API contemporanee' : 'Recuperi contemporanei', all: step === 'jev' ? 'Tutti i ruoli e le aziende eleggibili' : 'Tutte le descrizioni recuperabili', refresh_stale: 'Aggiorna anche descrizioni scadute', force: 'Riprova gli errori, rispettando i blocchi della fonte', revisit: 'Rivedi anche le aziende già classificate', company_limit: 'Numero di aziende del campione', all_companies: "Tutte le aziende dell'archivio", mode: step === 'jev' ? 'Modalità Jev' : 'Modalità LLM remoto'};
  const label = el('label', labels[key]);
  let input;
  if (key === 'source' || key === 'mode') {
    input = el('select');
    const choices = key === 'mode' ? [['preview', 'Anteprima senza spesa'], ['execute', 'Esegui con API a pagamento']] : (step === 'collection' ? data.collection_sources : ['', ...data.description_sources]).map(v => [v, v || 'Tutte le fonti']);
    for (const [value, text] of choices) { const option = el('option', text); option.value = value; input.append(option); }
  } else {
    input = el('input');
    input.type = ['all', 'refresh_stale', 'force', 'all_companies', 'revisit', 'continue_after'].includes(key) ? 'checkbox' : 'number';
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
  if (action) {
    for (const key of action.fields) $('pipeline-fields').append(pipelineField(key, 'step', id));
    const sequence = pipelineData.controls.sequence;
    if (sequence.includes(id) && id !== sequence.at(-1)) {
      const label = el('label', 'Continua da qui con i passaggi successivi');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.name = 'continue_after';
      label.append(checkbox); $('pipeline-fields').append(label);
      const chain = el('div'); chain.hidden = true;
      chain.append(el('p', sequence.slice(sequence.indexOf(id)).map(k => pipelineData.controls.actions[k].label).join(' → ')));
      chain.append(el('p', 'I passaggi facoltativi vengono saltati. La sequenza si ferma su errore o risultato parziale. Il recupero successivo considera tutte le descrizioni eleggibili, e i passaggi a pagamento della sequenza usano la modalità scelta qui sotto.'));
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
  const gated = form.querySelector('select[name="mode"][data-scope="step"]');
  $('pipeline-start').textContent = paid ? sequence ? 'Avvia sequenza con API a pagamento' : 'Avvia API a pagamento' : sequence ? 'Avvia sequenza' : gated ? 'Prepara anteprima' : 'Avvia passaggio';
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
