# JobHunter

JobHunter raccoglie opportunità, valuta separatamente aziende e ruoli e conserva le preferenze
dell'utente senza confonderle con i giudizi automatici.

## Language

**Categoria aziendale**:
Uno dei settori in cui opera un'azienda. Un'azienda può avere più categorie contemporaneamente.
_Avoid_: Categoria primaria, settore unico

**Giudizio Jev**:
Valutazione automatica tipizzata di un ruolo o delle categorie aziendali, distinta dalla decisione
dell'utente.
_Avoid_: Decisione finale, revisione umana

**Indeciso dopo Jev**:
Ruolo con evidenza presente ma insufficiente o contrastante per produrre un `keep` o `exclude`
automatico affidabile.
_Avoid_: Errore Jev, scarto

**Bloccato dai dati**:
Ruolo che non può essere valutato semanticamente perché mancano mansioni utilizzabili.
_Avoid_: Indeciso, escluso

**Revisione Codex**:
Sessione guidata in cui Codex presenta batch di ruoli, l'utente chiarisce preferenze e conferma
decisioni o criteri riutilizzabili.
_Avoid_: Giudizio automatico, decisione di Codex
