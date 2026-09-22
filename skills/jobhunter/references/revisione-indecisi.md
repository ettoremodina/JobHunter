# Revisione degli indecisi di Jev

Usa questa procedura quando l'utente vuole capire e risolvere le ambiguità rimaste dopo Jev.

## Avvio e ripresa

```powershell
python main.py codex-session start --mode indecisi --batch-size 5
```

Il risultato contiene l'ID della sessione e il primo batch compatto. Riprendi senza rileggere i casi già trattati con `python main.py codex-session show SESSION_ID`.

La popolazione è congelata all'avvio e comprende solo risultati Jev correnti con decisione `review` e descrizione non vuota. Non reinserire gli annunci senza descrizione: sono un problema di acquisizione, non una preferenza da chiedere all'utente.

## Analisi del batch

Raggruppa i casi per dubbio sostanziale, non solo per parole comuni. Usa motivazione, evidenza e informazioni mancanti fornite da Jev. Poni al massimo tre domande per turno, privilegiando quelle che chiariscono più casi.

Espandi un caso solo se la scheda compatta non basta: `python main.py codex-session expand SESSION_ID OPPORTUNITY_ID`.

Classifica ogni risposta come:

- scelta sul singolo annuncio: usa `feedback COMPANY_ID STATUS --opportunity OPPORTUNITY_ID --reason other --note "..."`;
- preferenza generale confermata: salvala nell'evento della sessione;
- ipotesi o considerazione: salvala solo nelle note della sessione;
- possibile regola regex: prepara un'anteprima con `review-rule`, misura transizioni ed esempi e applicala solo dopo conferma;
- possibile modifica al prompt Jev: documenta esempi positivi e negativi e chiedi conferma prima di modificare configurazione o codice.

## Registrazione del progresso

Scrivi un file JSON UTF-8 temporaneo:

```json
{
  "event_id": "identificatore-stabile-del-turno",
  "reviewed_ids": ["opportunity-id"],
  "notes": [
    {"kind": "proposal", "text": "Possibile modifica da verificare", "item_ids": ["opportunity-id"]}
  ],
  "memories": [
    {"kind": "preference", "id": "preferenza-stabile", "text": "Preferenza confermata dall'utente"}
  ]
}
```

Registra con `python main.py codex-session record SESSION_ID EVENT.json`. `event_id` rende sicuro ripetere il comando. Metti in `memories` soltanto affermazioni confermate come riutilizzabili; le note non diventano regole attive.

Concludi indicando casi risolti e aperti, decisioni puntuali scritte, memorie confermate e modifiche alla pipeline soltanto proposte.
