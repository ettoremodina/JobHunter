# Configurazione

Il codice legge configurazioni esplicite dalla cartella `config/`. Le preferenze personali e le prove del profilo restano in `user_context/`. Le credenziali arrivano dall'ambiente o dal file di segreti configurato e non devono entrare nella documentazione.

## File attivi

| Ambito | File | Contenuto |
|---|---|---|
| Applicazione | `config/app.json` | Database, porta, fonti, limiti generali e directory degli snapshot |
| Raccolta ampia | `config/sweep.json` | Concorrenza, pagine e timeout delle query JobSpy |
| JobSpy | `config/jobspy.yaml` | Board, query, località e finestra temporale |
| Altre fonti | `config/airtable.yaml`, `config/sites/*.yaml` | Endpoint e navigazione per fonte |
| Descrizioni | `config/descriptions.json` | Host ammessi, worker, timeout, scadenze e rinvii |
| Filtri dei ruoli | `config/role_filters.json` | Famiglie professionali, esclusioni, esperienza, lingue e categorie preferite |
| Settori | `config/categories.json` | Vocabolario delle categorie aziendali e segnali lessicali |
| Geografia | `config/geography.json`, `config/cities.json` | Paesi, continenti, città e alias |
| Jev | `config/system_one.json`, `config/system-one-questions.json` | Endpoint, modello, soglie, concorrenza e domande tipizzate |
| Schede Qwen | `config/remote_llm.json`, `config/prompts/remote-*.txt` | Provider, modello, prompt, limiti e task disponibili |
| Risposte strutturate | `config/remote-*-schema.json` | Forma JSON accettata per selezione e schede |
| Campi delle schede | `config/job_summary_fields.json`, `config/job_field_sections.json` | Etichette e sezioni mostrate nella dashboard |
| Pipeline web | `config/pipeline_ui.json` | Sequenza, limiti, campioni e frequenza di aggiornamento |
| Revisione | `config/review_questions.json` | Gruppi usati dal comando storico `review-questions` |
| Esperimenti | `config/calibration.json`, `config/prompts/remote-calibration.txt` | Campione e modello della calibrazione separata |

## Invalidazione

`search_eligibility` conserva gli esiti dei filtri locali. Cambiamenti a `role_filters.json` o alle funzioni di estrazione usate dal filtro rendono obsolete le righe interessate. La lettura aggiorna soltanto l'ambito richiesto, mentre Metriche e gli altri report globali possono validare l'intero archivio.

Jev salva impronte separate per giudizio sul ruolo e categoria aziendale. Una modifica alle domande, alle soglie, al catalogo categorie o agli input rende obsoleto soltanto il risultato relativo.

Le schede Qwen includono modello, parametri, prompt, schema e input nella chiave di cache. Cambiare uno di questi elementi richiede una nuova scheda. Le decisioni personali e gli snapshot originali non vengono cancellati.

## Credenziali

`system_one.json` e `remote_llm.json` dichiarano il nome della variabile d'ambiente e, dove previsto, il file locale di segreti. Anteprime e letture non devono richiedere la chiave. Solo un comando con `--execute` o l'equivalente conferma nella dashboard può inviare dati a un provider a pagamento.

## Controlli prima di modificare

1. Cerca il file in `jobhunter/`, `scripts/` e `tests/` per trovare tutti i consumatori.
2. Aggiorna insieme configurazione, validazione e test.
3. Usa una copia del database per prove che possono scrivere dati derivati.
4. Esegui la suite completa e il controllo browser.
