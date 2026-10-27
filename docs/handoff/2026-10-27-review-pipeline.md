# Handoff: review progetto pipeline e opportunità mobilità urbana

Stato al 2026-10-27, ricavato dalla chat Claude Code omonima (19/10, sul PC, cartella `LIFE\WebScraper`, poi archiviata). Progetto in pausa.

## Cosa è stato fatto

La chat ha fatto una review della pipeline (con revisori in parallelo, anche sulla logica di selezione e tier) e ne ha applicato i primi due passi. Test: **190 su 190 passano**, compresi 11 nuovi, più il controllo browser della pipeline.

**Passo 1, niente più stati fantasma**
- Un PID riusato da Windows non conta più come vivo: si confronta l'orario di avvio del processo con l'inizio del lavoro (`jobhunter/operations/progress.py`). La raccolta del 24/9, rimasta `running`, ora risulta non attiva e la dashboard può di nuovo avviare i passaggi.
- Una riga rimasta `running` con il PID della dashboard stessa viene chiusa come interrotta.
- Lo stato finale di un job si scrive con nuovi tentativi, così un database occupato non lo lascia `running`.
- I report del recupero descrizioni e di `collect-all` si chiudono come `failed` o `interrupted` in ogni caso.
- Gli errori di lettura di urllib3 sono trattati come errori di rete.

**Passo 2, un errore riguarda solo il suo record**
- Jev e Qwen proseguono dopo risposta troncata, 400 o errore di rete su un singolo record; ritentano da soli le richieste mai partite (DNS, connessione rifiutata).
- Interruttore comune: ferma la run su 401/402/403, su 10 errori di fila o su oltre metà chiamate fallite dopo almeno 20. Soglie nel blocco `breaker` di `config/system_one.json` e `config/remote_llm.json`.
- Una run fermata prima della fine o che non salva nulla chiude `failed`; «Sequenza completata» compare solo se tutto è riuscito.

Documentazione allineata e test dei link verde. Report di review aggiornato con lo stato dei passi 1-2.

## Stato git

Commit `e71c5c1` su `main` **solo in locale**, non pushato; working tree pulito.

## Da fare

- Passo 3: il **429 di LinkedIn**.
- Passo 5: **comando notturno unico**, per una notte davvero autonoma.
- Passo 4: non l'ho trovato nella parte di trascrizione che ho potuto leggere; controllare il report di review locale.
- Sicurezza: la repo risulta pubblica e nei commit `68b7efe`, `21f065b`, `c6775f4` (aprile) c'è un `.env` con un token Telegram; sono anche tracciati vecchi snapshot SQLite e JSON grezzi in `data/`. Revocare il token e decidere privata o riscrittura della history. Altri punti della review (22/10): il pool delle descrizioni non si ferma su errore (`jobhunter/acquisition/descriptions.py`, `recover`).

## File toccati

`jobhunter/operations/progress.py`, moduli di acquisizione e dei provider Jev/Qwen, `config/system_one.json`, `config/remote_llm.json`, documentazione in `docs/`, 11 test nuovi, report di review.
