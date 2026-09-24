# Primi passi

*[Read in English](getting-started.md)*

Questa guida porta da una copia appena clonata ai primi annunci giudicati: installazione, profilo, filtri e primo avvio. Per capire come funziona la pipeline, apri la panoramica illustrata in [`docs/jobhunter-overview.html`](jobhunter-overview.html).

JobHunter gira sul tuo computer. Profilo, preferenze, archivio e chiavi API non lo lasciano mai: ogni file personale è ignorato da Git.

> **Preferisci farti guidare?** Chiedi al tuo agente di programmazione (Codex, Claude Code o simili) di *«leggere `skills/jobhunter-setup/SKILL.md` e seguirlo»*. Ti fa qualche domanda e scrive per te i file descritti qui sotto.

La dashboard e i messaggi da riga di comando sono in italiano. I file di configurazione accettano testo in italiano o in inglese.

## 1. Installazione

Servono Python 3.11 o successivo e Git.

```bash
git clone <url-della-repository> jobhunter
cd jobhunter
python -m venv .venv
```

Attiva l'ambiente: su Windows con `.venv\Scripts\activate`, su macOS e Linux con `source .venv/bin/activate`. Poi:

```bash
pip install -r requirements.txt
playwright install chromium
python main.py init
```

`init` crea il database locale in `data/` e copia i file di esempio da `examples/` dove il tuo file non c'è ancora. Non sovrascrive mai un file che hai già.

## 2. I tuoi file personali

Dopo `init` questi file sono tuoi. Partono come esempio completo di un candidato inventato: sostituiscine il contenuto con il tuo.

| File | Cosa controlla |
|---|---|
| `user_context/portfolio-evidence.md` | Esperienze e progetti, scritti come fatti. La fonte da cui derivano tutti gli altri. |
| `user_context/llm-selection-profile.md` | Chi sei, cosa cerchi, cosa escludere, i vincoli. Contesto per le schede e per l'agente di revisione. |
| `config/role_filters.json` | Il filtro a regole: titoli esclusi, esperienza massima richiesta, lingue, settori preferiti. |
| `config/jobspy.yaml` | Le ricerche su LinkedIn e Indeed: titoli, paesi, età degli annunci. |
| `config/system-one-questions.json` | Le domande a cui risponde Jev: quali mansioni ti vanno bene, quali famiglie di lavoro escludi. |
| `config/review_questions.json` | Gruppi di titoli su cui l'agente di revisione ti fa domande. |
| `user_context/selection/regole.md` | Le regole di revisione che confermi in chat. All'inizio è vuoto. |
| `.env.local` | Le chiavi API. |

Anche tutto quello che sta in `data/` (archivio, pagine scaricate, report) resta locale.

## 3. Scrivi il profilo

Parti da `user_context/portfolio-evidence.md`: ruoli, progetti, strumenti e risultati, un fatto per riga. Resta sui fatti: gli altri file derivano da qui.

Poi scrivi `user_context/llm-selection-profile.md` in quattro sezioni brevi:

- **Chi sei:** titolo di studio, anni di esperienza, il filo che lega il tuo lavoro.
- **Cosa cerchi:** i tipi di ruolo adatti e i settori che preferisci.
- **Cosa escludere:** le famiglie di lavoro che non vuoi, descritte dal lavoro e non dal titolo.
- **Vincoli:** esperienza massima richiesta, lingue, stage, tutto ciò che è un no netto.

## 4. Imposta il filtro a regole

`config/role_filters.json` decide cosa scartare gratis, prima di chiamare qualsiasi modello. **Metti qui solo esclusioni certe.** Tutto ciò che richiede un giudizio spetta a Jev (punto 6).

| Campo | Significato |
|---|---|
| `exclude_title_patterns` | Espressioni regolari con un nome. Un titolo che ne soddisfa una viene scartato, per esempio `seniority` o `management`. |
| `exclude_title_exceptions` | Per un gruppo qui sopra, un pattern che salva il titolo. Per esempio un titolo meccanico che parla di «data». |
| `user_title_rules` | Regole sui titoli aggiunte dopo le revisioni. |
| `preferred_title_pattern` | Titoli della tua famiglia di riferimento. Hanno priorità, ma non vengono tenuti in automatico. |
| `primary_title_pattern` | Un insieme più stretto: ruoli abbastanza forti da contare anche in un'azienda fuori dai settori preferiti. |
| `max_required_years` | Il massimo di anni di esperienza **obbligatoria** che accetti. Gli anni «preferibili» non escludono mai. |
| `allowed_languages` | Le lingue in cui puoi lavorare. Le altre escludono solo quando l'annuncio le rende obbligatorie. |
| `preferred_categories` | I settori preferiti. Usa i nomi esattamente come sono in `config/categories.json`. |

All'inizio lascia gli altri campi come sono. Dopo una modifica i verdetti del filtro si aggiornano da soli: non serve rilanciare nulla.

## 5. Scegli fonti e ricerche

Le fonti sono in `config/app.json`, sotto `sources`. Ognuna ha `"enabled": true` o `false`.

| Fonte | Cosa porta |
|---|---|
| `jobspy` | LinkedIn e Indeed, guidati da `config/jobspy.yaml`. |
| `airtable` | ClimateTechList, una bacheca di aziende climatiche. Include il settore. |
| `ats` | Le bacheche pubbliche (Greenhouse, Lever, Workday...) delle aziende che ti interessano: testo completo e data esatta. L'elenco si costruisce con `python main.py ats-discover`. |
| `climatebase.org` | Climatebase, letta con un browser automatico. Disattivata da settembre 2026: il sito blocca i browser automatici. |

Le due bacheche climatiche servono solo se ti interessa quel settore; altrimenti disattivale.

In `config/jobspy.yaml` imposta:

- `search_queries`: i titoli da cercare;
- `locations`: i paesi;
- `site_names`: `linkedin`, `indeed` o entrambi;
- `hours_old`: l'età massima degli annunci in ore (168 è una settimana);
- `results_wanted`: i risultati per ricerca.

Ogni titolo viene cercato in ogni paese su ogni bacheca, quindi il numero di ricerche cresce in fretta. Parti in piccolo.

## 6. Adatta le domande di Jev

Jev è il modello che legge le mansioni e risponde a domande precise con probabilità. Le domande sono in `config/system-one-questions.json` e contengono il tuo profilo, quindi riscrivile per il tuo:

- in `selection → mansioni_compatibili`, descrivi il lavoro che ti va bene (`true`) e quello che non ti va bene (`false`);
- in `selection → famiglia_esclusa`, elenca le famiglie di lavoro che escludi, un'opzione per ciascuna;
- in `selection → seniority_fuori_profilo`, tieni il numero di anni coerente con `max_required_years`.

**Non cambiare le chiavi.** Il codice legge i nomi delle domande (`mansioni_descritte`, `mansioni_compatibili`, `famiglia_esclusa`, `posto_per_studenti`, `seniority_fuori_profilo`, `prova`) e le opzioni `nessuna`, `produzione_manutenzione` e `officina_ricambi`. Le altre famiglie puoi aggiungerle o toglierle.

Le soglie che trasformano le probabilità di Jev in tieni, non so o scarta sono in `config/system_one.json`. Quelle predefinite sono un buon punto di partenza.

## 7. Aggiungi le chiavi API

Entrambe le chiavi sono facoltative:

| Chiave | Servizio | Senza |
|---|---|---|
| `TYPESAFE_API_KEY` | Jev di TypeSafe: giudica i ruoli e il settore delle aziende | Ogni ruolo che supera il filtro resta «non so». |
| `JOBHUNTER_API_KEY` | Qwen su Alibaba Cloud Model Studio: scrive le schede | Nessuna scheda. I verdetti non cambiano. |

Copia `.env.local.example` in `.env.local` e incolla ogni chiave dopo il suo `=`, senza virgolette né spazi. `.env.local` è ignorato da Git. Non mettere mai una chiave in un file di configurazione.

Una passata di Jev su un archivio di circa 23.000 annunci costa circa 1,20 $. Ogni comando a pagamento ha un'anteprima gratuita.

## 8. Primo avvio

Prima controlla i tuoi file per gli errori più comuni: espressioni regolari non valide, settori assenti dal vocabolario, chiavi delle domande rinominate, profili ancora uguali all'esempio. Se è tutto in ordine stampa `OK`.

```bash
python skills/jobhunter-setup/scripts/check_config.py
```

Parti con pochi annunci e controllali a mano prima di allargare.

```bash
python main.py collect jobspy --limit 20
python main.py fetch-descriptions --limit 50
python main.py system-one --limit 10
python main.py system-one --limit 10 --execute
python main.py serve
```

1. `collect` scarica qualche annuncio.
2. `fetch-descriptions` ne recupera il testo.
3. `system-one` senza `--execute` è un'anteprima gratuita. Mostra cosa verrebbe inviato, senza chiave e senza chiamate.
4. `--execute` fa le chiamate a pagamento.
5. `serve` apre la dashboard su <http://127.0.0.1:8000>. Su Windows puoi anche fare doppio clic su `Avvia JobHunter.pyw`.

Nella dashboard, apri **Aziende** per vedere le aziende e i loro verdetti, e **Metriche** per vedere perché gli annunci sono stati scartati. Se il filtro scarta ruoli che vuoi, correggi `role_filters.json`; se Jev li giudica male, correggi le sue domande.

Quando il campione ti convince:

- lancia `python main.py collect-all` per la raccolta completa;
- apri la tab **Pipeline**, avvia il passaggio successivo e spunta «Continua da qui con i passaggi successivi» per eseguire gli altri in sequenza.

## 9. Rivedi i ruoli incerti con un agente

I ruoli che Jev non sa decidere restano «non so» e ti aspettano. La skill in `skills/jobhunter/` permette a un agente di programmazione di rivederli con te a piccoli lotti. Fa domande, propone regole e registra solo ciò che confermi. Le regole confermate finiscono in `user_context/selection/regole.md`.

Per usarla, indica al tuo agente `skills/jobhunter/SKILL.md`, oppure copia la cartella nella cartella delle skill del tuo agente. Per esempio `.claude/skills/` per Claude Code o `~/.codex/skills/` per Codex. Dettagli in [conversazioni-codex.md](conversazioni-codex.md).

## I tuoi dati restano privati

I file personali, `.env.local` e `data/` sono elencati in `.gitignore`. Prima di pubblicare un fork, lancia `git status` e controlla che non compaia nessuno di loro.
