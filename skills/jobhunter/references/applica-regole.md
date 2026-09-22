# Applicare le regole confermate

Usa questa procedura quando l'utente chiede di applicare le sue regole, o di far decidere all'agente gli indecisi e i ruoli dei Tier A/B.

## Che cosa sei in questa sessione

Sei il giudice **agente**. La gerarchia sul singolo ruolo, dal più forte, è: utente → agente → Jev → regex. Il tuo verdetto sostituisce quello di Jev nel tier e nelle schede, ma non lo cancella: la catena mostra entrambi. Non puoi decidere un ruolo che l'utente ha già deciso, e la sessione non te lo propone.

Decidi **solo applicando una regola attiva** di `user_context/selection/regole.md`. Se nessuna regola copre il caso, non registrare un verdetto: il ruolo resta com'è e, se vale la pena, proponi all'utente una regola nuova. Il tuo gusto personale non è una regola.

## Avvio

```powershell
python main.py codex-session start --mode regole --batch-size 5
```

La popolazione è congelata all'avvio: indecisi di Jev con mansioni leggibili e ruoli compatibili delle aziende di Tier A e B-esperienza, meno quelli già decisi dall'utente o da te con le regole in vigore. Senza regole attive l'avvio fallisce: prima va confermata almeno una regola, in questa o in un'altra sessione. Ogni scheda riporta `tier`, il `verdetto` attuale e il giudizio di Jev. Espandi il testo originale solo se la scheda non basta: `python main.py codex-session expand SESSION_ID OPPORTUNITY_ID`.

## Decidere un batch

Per ogni ruolo:

- `keep` se una regola lo rende compatibile; su un indeciso, questo può far salire l'azienda di tier;
- `exclude` se una regola lo esclude; su un ruolo compatibile, l'azienda può scendere;
- nessun verdetto se nessuna regola si applica con certezza.

Mostra all'utente i verdetti che intendi registrare, con la regola citata e una motivazione di una riga, **prima** di registrarli. Registra solo quelli che l'utente non contesta.

## Registrazione

```json
{
  "event_id": "regole-turno-001",
  "reviewed_ids": ["opportunity-id-senza-verdetto"],
  "verdicts": [
    {"opportunity_id": "opportunity-id", "decision": "exclude", "rule_ids": ["R2"],
     "rationale": "Sviluppo front-end puro: R2"}
  ]
}
```

`python main.py codex-session record SESSION_ID EVENT.json`. I ruoli con verdetto contano già come rivisti; aggiungi a `reviewed_ids` quelli che hai guardato senza decidere. Un evento con anche un solo verdetto non valido viene rifiutato per intero.

## Confermare una regola nuova

Una regola nasce da una conversazione, in qualunque sessione, e diventa attiva solo quando l'utente la conferma esplicitamente. Prima di chiedere la conferma, mostra quali ruoli della sessione cambierebbero e due o tre esempi. Poi registrala nello stesso formato:

```json
{"event_id": "regola-R3", "rules": [{"id": "R3", "text": "Testo verificabile nelle mansioni, senza riferimenti al singolo annuncio."}]}
```

Usa il primo ID libero. Una regola esistente non si riscrive dalla chat: per cambiarla l'utente modifica `regole.md` a mano, oppure si registra una regola nuova e si cancella la vecchia dal file. In entrambi i casi i verdetti che citavano la versione precedente smettono di contare e tornano nella coda della prossima sessione `regole`.

## Chiusura

Riporta: verdetti registrati per regola (quanti keep, quanti exclude), aziende che hanno cambiato tier se l'utente lo chiede (`python main.py show COMPANY_ID`), casi lasciati senza verdetto e regole nuove soltanto proposte.
