# Mappa del codice e dei dati

Questa pagina risponde a due domande: dove modificare un comportamento e dove verificarne gli effetti.

## Ingressi

| Ingresso | Responsabilita |
|---|---|
| `main.py` | Avvia la CLI in `jobhunter/cli.py` |
| `Avvia JobHunter.pyw` | Avvia la dashboard Windows e gestisce la chiusura del server |
| `skills/jobhunter/` | Guida le conversazioni Codex sull'archivio |

## Pacchetto Python

| Percorso | Responsabilita |
|---|---|
| `jobhunter/workspace.py` | Interfaccia `Archive`, schema SQLite, importazione, ricerca e decisioni persistenti |
| `jobhunter/normalization.py` | Pulizia e identità dei record importati |
| `jobhunter/acquisition/` | Raccolta, descrizioni, profili aziendali e worker JobSpy |
| `jobhunter/evaluation/` | Filtri locali, Jev, categorie, Tier, schede Qwen e validazione |
| `jobhunter/operations/` | Sequenza della pipeline, lavori persistenti, stop e manutenzione |
| `jobhunter/exploration/` | Metriche, geografia, Debug, sessioni Codex e server della dashboard |
| `jobhunter/experiments/` | Esperimenti ripetibili esclusi dal percorso ordinario |

`Archive` è il modulo profondo condiviso da CLI, dashboard e test. I chiamanti usano metodi come `search`, `show`, `feedback` ed `evaluations`; schema e query restano nascosti dentro il modulo. Separare queste query in piccoli wrapper aggiungerebbe interfacce senza ridurre la complessità dei chiamanti.

## Dashboard

| Percorso | Responsabilita |
|---|---|
| `jobhunter/exploration/dashboard.py` | Route consentite, connessioni SQLite, risposte JSON e controlli sulle scritture |
| `dashboard/index.html` | Struttura delle viste e dialoghi |
| `dashboard/app.js` | Stato client, chiamate HTTP e rendering delle tab |
| `dashboard/style.css` | Layout, temi e stati visivi |

Il frontend non contiene un secondo motore di selezione. Legge i risultati dai moduli Python. La suddivisione futura degli asset deve seguire le viste reali, ma soltanto dopo che la revisione della tab Metriche è stabile.

## Dati persistenti

| Dato | Posizione | Regola |
|---|---|---|
| Aziende e annunci | `companies`, `opportunities` | Originali normalizzati, mai sostituiti dalle schede |
| Provenienza | `observations`, snapshot in `data/` | Conservare URL e momento dell'osservazione |
| Esiti locali | `search_eligibility` | Cache derivata da contenuto, regole e codice del valutatore |
| Categorie | `categories` | Più etichette per azienda, con metodo e motivo |
| Jev e schede | `enrichments` | Task, record, impronte, modello e risposta restano distinti |
| Decisioni personali | `feedback`, `feedback_detail` | Eventi reversibili, separati dai verdetti automatici |
| Lavori della dashboard | `pipeline_jobs`, `pipeline_cancellations` | Stato, parametri, avanzamento e richiesta di stop |
| Sessioni Codex | `data/codex-sessions/` | Popolazione congelata, note e avanzamento |
| Memoria di selezione | `user_context/selection/` | Preferenze, note e regole confermate in chat; restano locali |

I dettagli delle tabelle sono in [data-model.md](data-model.md).

## Dove intervenire

| Obiettivo | Moduli principali | Test iniziali |
|---|---|---|
| Aggiungere una fonte | `acquisition/collection.py`, `acquisition/sweep.py`, configurazione della fonte | `test_scraper_pipeline.py`, `test_board_worker.py` |
| Cambiare un filtro ruolo | `evaluation/selection.py`, `evaluation/languages.py`, `config/role_filters.json` | `test_product_selection.py`, `test_search.py` |
| Cambiare categorie o Tier | `evaluation/company_categories.py`, `evaluation/tier.py` | `test_company_axis.py`, `test_pipeline.py` |
| Cambiare Jev | `evaluation/system_one.py`, `config/system-one-questions.json` | `test_system_one.py` |
| Cambiare le schede Qwen | `evaluation/company_batch.py`, `evaluation/remote_llm.py` | `test_company_batch.py`, `test_remote_llm.py` |
| Cambiare una tab | `dashboard/`, route in `exploration/dashboard.py` | test browser e test del modulo letto |
| Cambiare sessioni Codex | `exploration/conversations.py`, `skills/jobhunter/` | `test_codex_conversations.py` |

## Script

`scripts/` contiene audit operativi ancora eseguibili. Le migrazioni una tantum e le simulazioni superate sono state rimosse; Git conserva le versioni precedenti. Un nuovo script deve avere un input esplicito, non leggere segreti se non necessari e dichiarare se modifica SQLite.
