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

**Revisione degli indecisi**:
Conversazione guidata sui casi che la pipeline non ha saputo decidere, usata per chiarire e
correggere i criteri automatici.
_Avoid_: Esplorazione della selezione, recupero descrizioni, giudizio automatico

**Insieme idoneo**:
Le aziende nei Tier A e B che hanno superato la pipeline automatica e sono disponibili per la
scelta personale. Non implica che l'utente le abbia salvate.
_Avoid_: Salvate, aziende approvate dall'utente

**Esplorazione della selezione**:
Conversazione facoltativa sull'insieme idoneo che aiuta l'utente a confrontare aziende e chiarire
i propri gusti senza cambiare automaticamente la pipeline.
_Avoid_: Revisione degli indecisi, ultimo filtro automatico

**Salvato manuale**:
Azienda o ruolo che l'utente ha messo da parte esplicitamente e che compare nella tab Salvate.
_Avoid_: Tier A, Tier B, keep di Jev

**Memoria di selezione**:
Preferenze e considerazioni confermate dall'utente, conservate per orientare conversazioni future.
Non equivale a una regola attiva della pipeline.
_Avoid_: Feedback puntuale, configurazione Jev
