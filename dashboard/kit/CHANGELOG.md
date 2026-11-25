# Kit delle app — versioni

Il kit sta in `templates/hub-app/web/kit/` di AgentWorkflow e si copia in `web/kit/` di ogni app, senza modificarlo lì. Ogni app scrive la versione che usa in `.agents/standard.json` (`"kit": "1.0"`). Quando esce una versione nuova, l'app Standard e la skill `standard-app` indicano le app rimaste indietro; aggiornare vuol dire ricopiare i file e leggere qui cosa è cambiato.

Le versioni seguono una regola semplice: il secondo numero sale per novità compatibili (si ricopia e basta), il primo quando cambia un nome di classe, di token o di funzione (serve una piccola modifica nell'app, descritta qui).

## 1.1 — 2026-11-25

Correzioni emerse applicando il kit a 7 app. Compatibile con la 1.0: basta ricopiare `kit.css` e `kit.js`.

- **Avvisi più leggibili nel tema chiaro:** `--warn` e `--accent-2` passano da `#a86b00` (4,4:1 sul bianco) a `#8f5b00` (almeno 4,5:1 anche su `--surface-2`).
- **`Kit.autosave` mette in fila i salvataggi:** mai due scritture in parallelo; una modifica arrivata durante un salvataggio parte subito dopo.
- **`Kit.autosave` mostra l'attesa:** «Modifiche da salvare…» finché il salvataggio non parte (classe `k-pending`).
- **`Kit.autosave` si ferma sui conflitti:** un errore con `error.stop = true` (per esempio un 409) non viene ritentato finché l'utente non modifica di nuovo.
- **`Kit.keys` lascia Invio e spazio a pulsanti e link:** se Invio è mappato, premere Invio su un pulsante lo preme ancora.
- `schedule.pending()` vale `true` anche mentre un salvataggio è in corso, così `Kit.refresh` non rilegge sopra una scrittura.

## 1.0 — 2026-11-24

Prima versione, nata dalle 50 regole tenute in Standard e dallo stile scelto il 24/11.

- **Stile (UI-02):** Mezzanotte con pulsanti ambra per lo scuro, Notte verde per il chiaro. Token per ruolo: `--bg`, `--surface`, `--surface-2`, `--line`, `--line-strong`, `--text`, `--text-2`, `--text-3`, `--accent`, `--on-accent`, `--accent-2`, `--ok`, `--warn`, `--bad`, `--cat-1`…`--cat-5`; forma: `--radius`, `--pad`, `--gap`, `--heading`, `--size`.
- **Componenti CSS:** barra in alto, pulsanti (`k-btn`, `k-primary`, `k-danger`), tag, chip con conteggio, schede, campi, righe con selezione luminosa, pannello sovrapposto con velo, menu, avvisi, stati vuoto/errore/caricamento, stato del salvataggio.
- **Funzioni JS (`window.Kit`):** `theme`, `panel`, `menu`, `keys`, `refresh`, `confirm`, `autosave`, `route` e `go`, `toast`, `copy`.
- **Contorno:** `Avvia App.cmd` che passa dall'hub, `manifest.webmanifest` con le icone, `DESIGN.md` breve.
