# Conversazioni Codex in JobHunter

Stato al 22 settembre 2026: sessioni, CLI, memoria, skill e ingressi dalla dashboard implementati. Il documento separa tre attività che in precedenza erano raccolte sotto il nome generico di «review Codex».

## I tre percorsi

### Salvate

La tab «Salvate» mostra soltanto aziende e ruoli messi da parte esplicitamente dall'utente. Il dato vive nel feedback SQLite ed è indipendente dai verdetti automatici. Un `keep` di Jev o un Tier A non equivale a un salvataggio.

### Revisione degli indecisi

È una conversazione sulla qualità e sui confini della pipeline. Il corpus contiene casi con mansioni disponibili che regex e Jev non hanno deciso. Gli annunci privi di descrizione restano bloccati dai dati e rientrano soltanto quando viene recuperata evidenza sufficiente.

Codex raggruppa i casi, mostra esempi e controesempi e chiede chiarimenti all'utente. Ogni risposta viene classificata prima di produrre modifiche:

| Tipo di risposta | Destinazione proposta |
|---|---|
| Criterio letterale, stabile e verificabile nel testo | Regex o regola deterministica |
| Distinzione che richiede capire le mansioni | Domanda, criterio o composizione Jev |
| Giudizio valido soltanto per quel caso | Feedback sul ruolo o sull'azienda |
| Gusto utile per confrontare alternative già valide | Memoria di selezione, non filtro attivo |
| Informazione assente dalla fonte | Coda di recupero dati, non domanda di preferenza |

Codex prepara una proposta e ne simula l'impatto sul corpus congelato. Solo una conferma esplicita può cambiare configurazioni o criteri. Dopo l'applicazione si invalidano e rieseguono soltanto i giudizi interessati. Le decisioni manuali non vengono riscritte.

### Esplorazione della selezione

È uno strumento facoltativo per navigare le aziende nei Tier A e B quando il volume resta alto. Non completa la pipeline e non tratta casi incerti: lavora sull'insieme già considerato idoneo.

L'utente definisce uno scope con filtri come paese, città, categoria, Tier e ricerca testuale. Codex presenta piccoli batch, confronta aziende e ruoli, chiarisce differenze e può registrare salvataggi o scarti puntuali. Le considerazioni generali alimentano la memoria di selezione, ma non diventano automaticamente criteri di esclusione.

### Applicazione delle regole (dal 22 settembre 2026)

Le regole generali che emergono da una conversazione, e che l'utente conferma esplicitamente, finiscono in `user_context/selection/regole.md` con un ID stabile (R1, R2…). In una sessione `regole` l'agente le applica agli indecisi di Jev e ai ruoli compatibili delle aziende di Tier A e B-esperienza, e registra un verdetto tieni o scarta che cita almeno una regola.

Sul singolo ruolo vale la gerarchia **utente → agente → Jev → regex**: il giudice più in alto che ha deciso vince, e la catena mostra ancora gli altri. Una decisione dell'utente (`feedback` saved/discarded sul ruolo) vince su tutto e non scade. Un verdetto dell'agente smette di contare quando cambia il testo dell'annuncio o quello di una regola citata, e il ruolo torna nella coda della sessione `regole` successiva. Nessuna sessione ripropone ruoli già decisi da uno dei due. Dettagli in `DESIGN.md` §3.

## Un'unica skill, tre modalità

La skill JobHunter rimane il punto d'ingresso e riconosce tre intenti espliciti:

- `rivedi gli indecisi`: apre o riprende una sessione di revisione degli indecisi;
- `esplora la selezione`: apre o riprende una sessione sulle aziende Tier A e B entro lo scope richiesto;
- `applica le regole`: apre una sessione `regole` in cui l'agente decide i ruoli citando le regole confermate.

La skill non carica l'intero archivio in chat. La CLI crea un manifest con popolazione, impronte dei criteri e schede compatte congelate; i turni successivi portano nella conversazione soltanto il batch non ancora trattato.

Comandi principali:

```powershell
python main.py codex-session start --mode indecisi --batch-size 5
python main.py codex-session start --mode selezione --batch-size 5 --tier A --country Italia
python main.py codex-session start --mode regole --batch-size 5
python main.py codex-session show SESSION_ID
python main.py codex-session expand SESSION_ID ITEM_ID
python main.py codex-session record SESSION_ID EVENT.json
```

`expand` accetta `--opportunity OPPORTUNITY_ID` quando `ITEM_ID` è un'azienda della selezione. `record` è idempotente tramite `event_id` e accetta ID revisionati, note di sessione, memorie e regole esplicitamente confermate e, solo nelle sessioni `regole`, i verdetti dell'agente. Valida l'intero evento prima di scrivere: un verdetto su un ruolo deciso dall'utente, o senza una regola attiva, fa rifiutare tutto l'evento.

## Strategia per contenere i token

1. Applicare prima filtri e ordinamenti deterministici nel database.
2. Caricare una sola scheda aziendale con i ruoli pertinenti, senza duplicare la descrizione aziendale.
3. Usare metadati e schede Qwen per il primo confronto.
4. Aprire testo originale ed evidenze soltanto per casi ambigui o scelti per l'approfondimento.
5. Mostrare batch piccoli e salvare su disco avanzamento e ID già letti.
6. Riprendere una sessione dal suo stato locale, senza ricostruirla dalla cronologia della chat.

## Persistenza

Le responsabilità restano separate:

- SQLite conserva feedback, salvataggi, scarti ed eventuali valutazioni puntuali;
- `user_context/selection/preferences.md` contiene preferenze confermate e stabili, leggibili dall'utente;
- `user_context/selection/regole.md` contiene le regole attive che l'agente applica; modificarle o cancellarle fa scadere i verdetti che le citano;
- `enrichments` (task `agent:selection`) conserva i verdetti dell'agente con la regola citata e l'impronta del testo;
- `user_context/selection/notes/` contiene considerazioni e confronti che vale la pena ricordare ma che non sono regole attive;
- `data/codex-sessions/<session-id>/` contiene manifest, scope, hash, avanzamento, domande e artefatti tecnici riproducibili.

Una nota Markdown non modifica regex o Jev. La revisione degli indecisi può promuoverla a proposta, mostrarne l'impatto e collegare la modifica confermata alla nota originaria.

## Interfaccia

Non c'è una nuova tab. «Salvate» rimane semplice. Nella tab «Pipeline» due schede preparano il testo per avviare in chat la revisione degli indecisi o l'esplorazione della selezione. Non eseguono modelli, non creano la sessione e non trasformano la dashboard in un secondo client di chat.
