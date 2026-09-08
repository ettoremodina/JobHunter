# GLM via API: selezione e sintesi

L'integrazione remota è opzionale e separata da Ollama. La configurazione iniziale usa Z.ai, modello `glm-5.3-flash`, effort `low`. Non sono state effettuate chiamate a pagamento durante la preparazione. La qualità reale e la disponibilità del modello sull'account richiedono un pilot.

## Configurazione e chiave

`config/remote_llm.json` contiene endpoint, modello, parametri, timeout, limite di input, massimo output, limite del batch, percorsi di prompt e profilo. Il protocollo implementato è Chat Completions con bearer token, senza streaming o strumenti. Altri provider sono possibili se compatibili con quel protocollo e con i parametri configurati; non vengono scelti automaticamente.

Inserire la chiave nella variabile ambiente `JOBHUNTER_API_KEY` oppure nel file `.env.local`, con una riga `JOBHUNTER_API_KEY=...`. L'ambiente ha precedenza. Il file supporta semplici assegnazioni, valori eventualmente tra virgolette e righe commentate, non espansioni di variabili. Il modello non legge o esporta la chiave. Non inserirla nei prompt, nel profilo o nei JSON di configurazione.

`.env.local.example` è il modello senza segreti; `.env.local` e `data/remote-llm/` sono ignorati da Git. Questa integrazione non usa il vecchio `.env`, che risulta già versionato nel progetto. Non copiarvi la nuova chiave.

Non occorrono pacchetti aggiuntivi: HTTP, JSON, cache SQLite e gestione dei file usano la standard library. Un SDK o un framework agentico non è necessario per tre operazioni di classificazione/sintesi.

## Comandi

Anteprima, senza chiave e senza chiamate remote:

```powershell
python main.py llm selection --limit 3
python main.py llm job-summary --limit 3
python main.py llm company-summary --limit 3
```

Le anteprime indicano gli ID e la dimensione in caratteri, non una stima economica. Possono aggiornare la cache locale dei filtri, ma non modificano le regole. `--record-id ID` permette di scegliere un annuncio o, per company-summary, un'azienda. `--llm-config PATH` seleziona una configurazione diversa.

Dopo aver inserito la chiave, una chiamata esplicita avvia il pilot:

```powershell
python main.py llm selection --limit 3 --execute
python main.py llm job-summary --limit 3 --execute
python main.py llm company-summary --limit 3 --execute
```

Il limite attuale è 50 richieste per esecuzione, configurabile. I task sugli annunci partono dai non esclusi dai filtri attivi. Le regex sperimentali delle simulazioni non vengono applicate da questi comandi. Se si vuole verificare anche gli esclusi locali, occorre preparare separatamente quel campione: non si promette copertura dell'intero archivio con questo pilot.

## Prompt e profilo

- `remote-selection.txt`: confronto semantico tra mansioni e profilo, con keep/exclude/review e citazioni.
- `remote-job-summary.txt`: sintesi breve, attività, requisiti e condizioni con evidenze; preservare numeri, negazioni, visti e requisiti preferenziali.
- `remote-company-summary.txt`: cosa produce/offre l'azienda, per chi e in quale ambito. Nessun profilo personale inviato per le sintesi.
- `user_context/llm-selection-profile.md`: preferenze personali confermate, separate dalle istruzioni generiche del classificatore. Sostituire il file per un'altra persona.

Un modello più potente può interpretare meglio il contesto, ma non conosce le preferenze dell'utente se non gli vengono fornite. I prompt quindi restano precisi sul risultato e sulle evidenze, senza lunghi elenchi di regex o istruzioni di ragionamento. Non si impone una quota di esclusioni: il target sotto mille non giustifica scartare opportunità pertinenti.

## Persistenza, errori e costi

Gli originali rimangono nel database; le sintesi non sovrascrivono descrizioni o dati aziendali. La selezione usa sempre la fonte originale normalizzata, non una sintesi generata. I risultati validati sono nella tabella esistente `enrichments`, con task `remote:selection`, `remote:job-summary` o `remote:company-summary`. I report in `data/remote-llm/` espongono risultati, ID, uso token restituito dal provider ed eventuale errore.

La cache dipende da fonte inviata, prompt, profilo quando necessario, modello, endpoint, parametri e versione di schema. Un nuovo avvio salta gli output già validi. Cambiare profilo o modello rende necessaria una nuova elaborazione; la vecchia versione è mantenuta nei report precedenti. Non c'è riscrittura periodica automatica.

Una richiesta per record, esecuzione seriale e nessun retry automatico: su 429, errore di rete, output non valido o troncato, il batch si ferma dopo aver salvato i progressi. Una risposta fallita può comunque essere stata fatturata. I corpi di errore HTTP e la chiave non vengono riportati nei log. Non viene stimato un costo in valuta senza un listino del provider verificato; i limiti di batch e output limitano il volume, non garantiscono un tetto monetario.

Le citazioni devono esistere esattamente nelle fonti fornite. Questo non dimostra che la parafrasi sia corretta o completa: il pilot deve controllare esclusioni errate e informazioni importanti omesse. Nessuna chiamata può attivare strumenti o navigazione; le risposte con tool calls sono rifiutate.

## Stato della preparazione

Pronti configurazione, caricamento chiave, client, tre prompt, comandi di anteprima/esecuzione, cache, report e test senza rete. Gli esiti sono derivati da revisionare: non modificano ancora la shortlist, i feedback o la dashboard. L'applicazione dei giudizi alla vista finale e la visualizzazione delle sintesi sono il passo successivo al pilot, per evitare che un prompt non validato nasconda annunci. Ollama resta disponibile per le funzioni esistenti.

## Proposte successive

1. **Domande ad alto impatto:** raggruppare i motivi dei review e proporre poche domande che risolvono molti annunci. Le risposte aggiornano il profilo, non diventano preferenze inventate dal modello.
2. **Scheda aziendale unica:** riassumere settore, ruoli pertinenti e motivi d'interesse; ridurre la lettura ripetuta di annunci della stessa azienda.
3. **Estrazione dei requisiti:** separare obbligatori, preferenziali e mancanti con citazioni, migliorando esperienza, lingua, visti e seniority rispetto al solo parsing regex.
4. **Individuazione di doppioni:** prima candidati con confronti locali di URL/titolo/azienda, poi giudizio LLM soltanto sui casi incerti. Conservare fonti e record originali.

Queste sono proposte, non funzioni già implementate.

Riferimento verificato: [API Chat Completion Z.ai](https://docs.z.ai/api-reference/llm/chat-completion). L'API documenta bearer token, endpoint generale e controllo reasoning_effort; per GLM-5.3-Flash il thinking non è disattivabile. L'integrazione imposta low anziché il default max. Il formato JSON viene richiesto nel prompt e validato localmente; non si assume che JSON mode sia supportato da tutti i provider/modelli multimodali.
