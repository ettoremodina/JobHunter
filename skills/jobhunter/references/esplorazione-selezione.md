# Esplorazione della selezione Tier A/B

Questa procedura è facoltativa: confronta aziende già giudicate valide, non corregge gli indecisi.

## Definisci un ambito

Concorda almeno un filtro utile: paese, città, categoria o ricerca testuale. Evita di riversare l'intero archivio in chat. Esempio:

```powershell
python main.py codex-session start --mode selezione --batch-size 5 --tier A --tier B-attesa --country Italia --category Energia
```

Sono disponibili `--query`, `--country`, `--city`, `--category` e ripetizioni di `--tier` tra `A`, `B-attesa`, `B-esperienza`. Senza `--tier` vengono inclusi tutti e tre. Riprendi con `python main.py codex-session show SESSION_ID`.

## Confronto a basso costo

Parti da riassunto aziendale, categorie, tier e riassunti dei soli ruoli compatibili. Confronta poche aziende per volta e spiega differenze concrete. Il tier è un giudizio della pipeline, non un salvataggio manuale.

Espandi la descrizione aziendale con `python main.py codex-session expand SESSION_ID COMPANY_ID`. Per un ruolo specifico aggiungi `--opportunity OPPORTUNITY_ID`.

Quando l'utente sceglie, usa `feedback COMPANY_ID saved` o `feedback COMPANY_ID discarded`; aggiungi `--opportunity` se la scelta riguarda un solo ruolo. Non dedurre una scelta da un commento comparativo.

Registra avanzamento, note e memorie con il contratto di `revisione-indecisi.md`. Una preferenza Markdown resta contesto per conversazioni future e non diventa automaticamente un filtro. Se l'utente vuole che un criterio venga applicato a tutti i ruoli, proponilo come regola: dopo la sua conferma esplicita registralo nel campo `rules` (vedi [applica-regole.md](applica-regole.md)) e offri una sessione `regole`.

Concludi con un confronto breve, le scelte effettivamente registrate e una sola proposta: altro batch, cambio dei filtri o fine della sessione.
