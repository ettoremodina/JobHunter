# Archivio, Salvate e ricerca mirata

## Consultazione locale

Usa `python main.py stats`, `search`, `show ID`, `saved`, `categories` e `places` secondo la richiesta. Una ricerca testuale non è una valutazione semantica. Conserva date, provenienza e incertezze; una scheda rappresenta un'azienda e contiene i suoi ruoli.

La tab Salvate mostra esclusivamente scelte manuali. `feedback COMPANY_ID saved --note "..."` salva un'azienda; aggiungi `--opportunity OPPORTUNITY_ID` per un ruolo. Gli altri stati sono `new`, `review`, `discarded` e `contacted`. `undo EVENT_ID` annulla un evento.

Prima di registrare una scelta, verifica gli ID con `show`. Non confondere un'azienda Tier A/B con una salvata manualmente.

## Ricerca online

Quando l'utente chiede un approfondimento, usa `research-brief ID` per individuare le domande, poi verifica online su fonti primarie. Salva fatti attribuiti con `python main.py evidence COMPANY_ID URL --note "Fatto verificato e data"`.

Il brief non è una ricerca già svolta. Distingui fatti verificati, inferenze e informazioni mancanti. Controlla se l'azienda esiste prima di usare `add-company`.

## Valutazioni e manutenzione

`assess ID FILE` importa una valutazione della chat ma non equivale a salvare o scartare. `categorize` assegna categorie senza cambiare feedback. `collect`, `collect-all`, `fetch-descriptions`, `system-one` e `llm` possono fare lavoro esterno o a pagamento: eseguili solo quando la richiesta li include e parti da anteprime o campioni quando disponibili.

Per i contratti aggiornati consulta `docs/jobhunter-v2.md`, `docs/remote-llm.md` e l'help della CLI. Non usare documentazione legacy quando contraddice i comandi attivi.
