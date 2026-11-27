# Da fare

Progetto **in pausa** (non serve ora). Le voci sotto riprendono quando si torna sul progetto. Nota: il PC può avere un `TO-DO.md` locale più aggiornato, da riconciliare con questo file.

## Review pipeline

- [ ] **Review progetto pipeline e opportunità mobilità urbana**: verificare e provare la pipeline. I passi 1-2 sono fatti (commit locale `e71c5c1`, non pushato); restano il 429 di LinkedIn (passo 3) e il comando notturno unico (passo 5). Dettagli nell'[handoff](docs/handoff/2026-10-27-review-pipeline.md).
- [x] **Pushare il commit `e71c5c1`** dal PC (`main` locale avanti rispetto a GitHub).

## Repo e dati

- [ ] **Sicurezza e backup della repo**: la repo risulta pubblica, la cronologia contiene un token Telegram e i dati locali non hanno copia.
  - [ ] Revocare il token Telegram da BotFather (`/revoke`).
  - [ ] Decidere la visibilità: rendere la repo privata, oppure riscrivere la history prima di tenerla pubblica (serve la tua autorizzazione esplicita). Vedi il [piano](../AgentWorkflow/PLANS/2026-11-24-applicazione-standard.md).
  - [ ] Backup dei dati in `data/`: oggi sono solo sul disco (ignorati da Git). Serve una repo dati privata in `personale/`, come da profilo.
  - [x] Push del commit del 24/11 (kit 1.0 e hub): `main` allineato a GitHub al 27/11.

## Aziende

- [ ] **Aziende mobilità urbana a Milano per il mio profilo**: la mappa è fatta (74 organizzazioni verificate in 6 tabelle, 25 a basso adattamento). Resta da scegliere dove puntare e caricarla nel progetto. Dettagli nell'[handoff](docs/handoff/2026-10-27-aziende-mobilita-milano.md).
