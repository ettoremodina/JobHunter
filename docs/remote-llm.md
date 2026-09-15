# LLM via API: selezione e sintesi

Per avviare il passaggio dalla web app, inclusi pilot su 100 aziende e sequenze automatiche, leggere [Pipeline dalla UI](pipeline-ui.md). I comandi qui sotto restano disponibili per i singoli task.

L'integrazione remota è opzionale e separata da Ollama. La configurazione corrente usa Alibaba Cloud Model Studio, endpoint internazionale QwenCloud, modello `qwen3.7-flash`, con thinking disabilitato e output JSON. Non sono state effettuate chiamate a pagamento durante la preparazione. La qualità reale e la disponibilità del modello sull'account richiedono un pilot.

## Configurazione e chiave

La console QwenCloud dell’utente mostra come Base URL `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`. La configurazione usa questo endpoint con `/chat/completions`; non serve inserire un workspace ID. Inserire la chiave in `.env.local` come `JOBHUNTER_API_KEY`. Il pilot dalla UI resta su 100 aziende, in anteprima per impostazione iniziale.

La ricerca web rimane disabilitata: l’integrazione esistente supporta il formato di ricerca OpenRouter e deve essere adattata prima di abilitare quella Alibaba. Sintesi e selezione usano le fonti già raccolte.

Fonti: [endpoint Alibaba](https://www.alibabacloud.com/help/en/model-studio/base-url), [capacità Qwen 3.7 Flash](https://www.alibabacloud.com/help/en/model-studio/qwen3-7-flash).

Alibaba può restituire i token senza un costo monetario: consultare il consuntivo Model Studio. L’assenza di costo nella risposta non significa costo zero.

`config/remote_llm.json` contiene endpoint, modello, parametri, timeout, limiti, percorsi di prompt e profilo. Il protocollo è Chat Completions con bearer token, senza streaming. Solo il task esplicito `company-research` abilita lo strumento di ricerca server di OpenRouter. Altri provider sono possibili per i task senza ricerca se compatibili con protocollo e parametri; non vengono scelti automaticamente.

Inserire la chiave QwenCloud nella variabile ambiente `JOBHUNTER_API_KEY` oppure nel file `.env.local`, con una riga `JOBHUNTER_API_KEY=...`. L'ambiente ha precedenza. Il file supporta semplici assegnazioni, valori eventualmente tra virgolette e righe commentate, non espansioni di variabili. Il modello non legge o esporta la chiave. Non inserirla nei prompt, nel profilo o nei JSON di configurazione.

`.env.local.example` è il modello senza segreti; `.env.local` e `data/remote-llm/` sono ignorati da Git. Questa integrazione non usa il vecchio `.env`, che risulta già versionato nel progetto. Non copiarvi la nuova chiave.

Non occorrono pacchetti aggiuntivi: HTTP, JSON, cache SQLite e gestione dei file usano la standard library. Un SDK o un framework agentico non è necessario per tre operazioni di classificazione/sintesi.

## Chiamate contemporanee e validazione parziale

### Diagnosi del punto 14, 15 settembre 2026

La verifica offline ha riprodotto e corretto tre difetti: una sintesi di ruolo richiesta ma
restituita `null` risultava riuscita; una risposta con ID mancanti non conservava
`rejected_answer` nel report; un elemento `choices` o `message` nullo sfuggiva alla
gestione degli errori della CLI. Ora la sintesi mancante produce `partial`, resta da
elaborare al prossimo avvio e non impedisce il salvataggio delle altre sintesi valide.
Il report conserva anche le risposte respinte per ID errati e i consumi ricevuti.
Le forme API inattese diventano un errore controllato `Invalid remote JSON response`.

Il prompt combinato distingue ora l'oggetto `jobs` in ingresso dall'array `jobs` in
uscita. La modifica cambia la firma del batch: i derivati precedenti restano nel
database ma la cache li considera obsoleti. Un successivo avvio esplicito potrebbe
quindi richiedere nuove chiamate anche per record già elaborati.

Le prove usano risposte simulate e SQLite temporaneo. Non erano disponibili report
reali nella worktree e non sono state fatte chiamate API. Endpoint e modello restano
quelli scelti nella memoria `remote-llm-pivot`; disponibilità sull'account, qualità
semantica e causa del malfunzionamento osservato dall'utente restano da verificare
su un campione reale. La correzione del prompt non dimostra un miglioramento della
qualità del modello. Nessuna modifica alle preferenze o alla selezione della pipeline.

Il passaggio combinato prepara ogni azienda sul thread che possiede SQLite e manda in parallelo solo la chiamata HTTP: i worker non toccano il database. Il numero di chiamate contemporanee si imposta in `config/remote_llm.json` (`company_batch.workers`, massimo `company_batch.max_workers`) e dal campo «Chiamate API contemporanee» nella card del passaggio. Il parallelismo riduce il tempo totale, non il costo: le chiamate restano una per azienda.

Una risposta non è più tutto-o-niente. Ogni giudizio di ruolo, ogni scheda di ruolo e la scheda aziendale vengono validati separatamente sulle proprie evidenze e salvati singolarmente. Se il modello inventa una citazione in una scheda, o omette la scheda aziendale richiesta, il resto della risposta già pagata resta salvato e la parte scartata viene ricontata come `rejected_jobs` nel report. Un errore di trasporto continua invece a fermare l'invio di nuove richieste: l'esito di fatturazione è ignoto e nessun tentativo viene ripetuto implicitamente.

Nella stessa richiesta ogni annuncio ha un proprio spazio di riferimenti (`J0-S3`, `J1-S7`, …): due annunci non possono più citarsi a vicenda per errore, perché entrambi i cataloghi partivano da `S0`.

## Comandi

Anteprima, senza chiave e senza chiamate remote:

```powershell
python main.py llm selection --limit 3
python main.py llm job-summary --limit 3
python main.py llm company-summary --limit 3
python main.py llm company-research --limit 3
```

Le anteprime indicano gli ID e la dimensione in caratteri, non una stima economica. Possono aggiornare la cache locale dei filtri, ma non modificano le regole. `--record-id ID` permette di scegliere un annuncio o, per company-summary, un'azienda. `--llm-config PATH` seleziona una configurazione diversa.

Dopo aver inserito la chiave, una chiamata esplicita avvia il pilot:

```powershell
python main.py llm selection --limit 3 --execute
python main.py llm job-summary --limit 3 --execute
python main.py llm company-summary --limit 3 --execute
python main.py llm company-research --limit 3 --execute
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

Le citazioni devono esistere esattamente nelle fonti fornite. Questo non dimostra che la parafrasi sia corretta o completa: il pilot deve controllare esclusioni errate e informazioni importanti omesse. I task ordinari non hanno strumenti; le tool calls da eseguire sul client sono sempre rifiutate.

## Stato della preparazione

Pronti configurazione Alibaba, quattro task, cache, report e test senza rete. La dashboard mostra le sintesi validate e i campi strutturati, conservando gli originali consultabili. I giudizi di selezione non modificano ancora shortlist o feedback: la loro applicazione richiede la validazione del pilot. Ollama resta disponibile. La dashboard verifica fonti, prompt e impostazioni correnti prima di mostrare una sintesi: output obsoleti o prodotti con una diversa configurazione sperimentale vengono nascosti.

## Schema degli annunci e scheda aziendale

`config/job_summary_fields.json` definisce attività, competenze obbligatorie e preferenziali, esperienza obbligatoria e preferenziale, istruzione, lingue, sede, modalità di lavoro, contratto, retribuzione, visto, trasferte e altre condizioni. Ogni campo vale `null` se manca, altrimenti una lista di indici dei `facts`, contando da zero. Testo e citazioni si salvano una volta sola. Esempio: `required_skills: null`, `preferred_skills: [0]` se il primo fatto dice che Python è preferenziale. Un campo vuoto non causa esclusioni. L'informazione estratta deve avere una citazione nel titolo o nella descrizione; eventuali metadati non citabili restano visibili nei campi originali della scheda.

La dashboard presenta una sola sintesi per azienda e ruoli distinti con i propri campi. I valori mancanti appaiono come “Non indicato”; le citazioni dei campi si leggono passando il puntatore sul valore. Le fonti aziendali e le descrizioni originali restano apribili.

“Possibile doppione” collega annunci della stessa azienda con titolo, località e descrizione normalizzata identici, oltre la lunghezza minima in `duplicates`. Nessun record viene cancellato, unito o nascosto. Testi quasi uguali non vengono rilevati; descrizioni generiche identiche possono produrre segnalazioni da controllare. Questi confronti esatti sono locali, senza costo LLM.

## Ricerca web delle descrizioni mancanti

`company-research` cerca soltanto aziende senza descrizione salvata e senza estratti aziendali già recuperati dagli annunci. Il solo settore non conta come descrizione. Usa `openrouter:web_search`, eseguito da OpenRouter nella stessa richiesta. Non serve una chiave Exa separata. Il risultato è già una sintesi aziendale utilizzabile nella scheda senza una seconda chiamata.

La configurazione iniziale usa Exa: massimo una ricerca, tre risultati e 3.000 caratteri per risultato. Le ricerche hanno un costo aggiuntivo ai token. I limiti contengono il volume, non garantiscono un tetto monetario. Aprire la dashboard o lanciare una sintesi ordinaria non avvia ricerche. `web_search.enabled: false` disabilita il task.

Il prompt preferisce fonti ufficiali e richiede verifica dell'identità con nome e sito disponibili. In caso di omonimia o informazioni insufficienti deve lasciare la sintesi vuota e dichiarare il dubbio. Nessun profilo personale viene inviato per la ricerca. Ogni fatto richiede URL ed estratto: il validatore li confronta con `url_citation.content` restituito dal provider. Un URL generato dal modello non basta. Se mancano contenuti verificabili nelle annotazioni, la risposta viene rifiutata senza retry.

Risposta, annotazioni, estratti, URL, modello e data sono salvati nel derivato `remote:company-research`, separatamente dagli originali. La cache include anche i parametri della ricerca. Il passare del tempo non rilancia automaticamente la ricerca: una scadenza periodica richiederà una politica esplicita.

Il servizio di ricerca server è in beta; la compatibilità effettiva con il modello e le annotazioni dell'account richiede un pilot reale. Riferimenti verificati: [ricerca web OpenRouter](https://openrouter.ai/docs/guides/features/server-tools/web-search), [GLM-5.3-Flash su OpenRouter](https://openrouter.ai/z-ai/glm-5.3-flash). Il formato JSON è richiesto dal prompt e validato localmente.

Resta da sviluppare la proposta di raggruppare i motivi dei `review` per formulare domande ad alto impatto sul profilo.

## Errori nel pilot aziendale

Le risposte JSON ricevute ma respinte dalla validazione vengono conservate solo nei report come `rejected_answer`, insieme ai token consumati. Non entrano nelle sintesi visibili o nelle valutazioni. Il pilot prosegue sugli altri record, conta `rejected` e termina con stato `partial` se esistono scarti, anche quando ha attraversato tutte le aziende. Errori API, di trasporto o risposte incomplete fermano invece la sequenza. Non ci sono retry automatici. `completed_companies` indica aziende attraversate, non aziende con tutti gli output validi.

## Formato vincolato e prove dalla fonte

Per Alibaba, selection, job-summary e company-summary usano schemi JSON esterni in config/remote-*-schema.json, inviati con strict=true. Gli ID ammessi per le prove sono generati dalle righe della fonte, comprese le informazioni strutturate di località, salario e contratto. Il modello restituisce gli ID; il validatore recupera gli estratti originali e respinge ID sconosciuti. Le descrizioni non vengono duplicate nel messaggio API.

La sintesi dei ruoli restituisce ogni fatto con field, text e quote. Il codice costruisce sections e indici dei campi per la dashboard, con la mappatura esterna config/job_field_sections.json. Nessun indice è più affidato al modello. Gli array vuoti dei campi nel vecchio formato sono normalizzati a null. Il formato salvato per la dashboard resta compatibile. La cache dipende anche da schema e catalogo effettivi; cambiare il protocollo invalida i risultati precedenti, che restano nei report.

Il controllo strutturale non dimostra che una frase italiana interpreti correttamente la fonte: restano necessari controlli semantici su requisiti, preferenze e negazioni. Un riferimento esistente può essere pertinente solo in parte. Nessuna selezione LLM viene applicata automaticamente alla shortlist durante il pilot.

## Arricchimento dopo la selezione

La pipeline aziendale seleziona prima i ruoli sopravvissuti ai filtri locali, più il campione di audit configurato. Solo keep/review attuali autorizzano company-summary e job-summary. Company-summary restituisce anche category, scelta dalle categorie esterne in config/categories.json; categoria e sintesi richiedono una sola chiamata. La categoria viene salvata con method=remote, senza sovrascrivere method=chat. Senza dati sufficienti resta Da classificare. La classificazione locale durante importazione è stata rimossa; resta disponibile come comando esplicito. Vedere docs/pipeline-ui.md per ordine e contatori.


## Richiesta unica per azienda
La pipeline GUI usa company_batch.run: selezione e sintesi dei ruoli mantenuti, descrizione e categoria aziendale condividono una richiesta. Profilo e contesto compaiono una volta. I risultati validi precedenti vengono riutilizzati. Le operazioni CLI individuali restano disponibili.
Tutti gli ID e le prove sono validati prima di una transazione unica. I token sono registrati una sola volta nel report aziendale, anche se la validazione fallisce. Errori del provider o di trasporto interrompono senza retry automatici; errori di validazione locale vengono registrati e la run prosegue con le aziende successive. Prompt e limiti sono esterni in config/prompts/remote-company-batch.txt e config/remote_llm.json, sezione company_batch.
Aziende oltre max_jobs o max_input_chars sono differite esplicitamente senza troncamenti. max_tokens limita la risposta; risposte incomplete non vengono accettate. Il campione di audit locale resta attivo. Le esclusioni restano proposte. Un pilot reale deve verificare copertura e consumi.


### Input compatto e conteggio richieste
Il batch usa `$defs` e `$ref` per inviare una sola definizione dello schema di ruolo. Gli ID delle prove vengono verificati per annuncio dal validatore locale, anche se nello schema condiviso sono stringhe. La descrizione e i metadati del ruolo vengono inviati nel solo catalogo delle prove; le categorie duplicate del contesto aziendale sono omesse. Il payload JSON del messaggio usa separatori compatti. Le preferenze personali non vengono modificate.
`submitted_jobs` conta gli annunci inclusi nelle richieste tentate; `evaluated_jobs` solo quelli con risposta validata e salvata. `api_calls` e `api_companies` contano le richieste tentate e le relative aziende; `rejected_companies` include errori e risposte rifiutate. `cached_jobs` conta annunci distinti riutilizzati in questa run. I risultati salvati restano riutilizzabili e non vengono eliminati per misurare il risparmio.


### Fonti mancanti
Gli schemi non emettono enum vuoti quando il catalogo delle prove e vuoto. La validazione locale continua a rifiutare qualsiasi identificatore non presente nel catalogo. Se la scheda aziendale non e richiesta, il relativo campo accetta solo null. Gli annunci senza descrizione ricevono solo la selezione; la sintesi e null e un giudizio valido viene riutilizzato anche senza scheda. La successiva acquisizione della descrizione invalida normalmente il risultato tramite fingerprint.
Gli errori HTTP conservano il messaggio JSON del provider, limitato in lunghezza e con la chiave API oscurata, senza retry automatici.


### Risposte superflue e validazione locale
Una scheda aziendale non richiesta viene ignorata senza invalidare i giudizi degli annunci, che restano validati prima del salvataggio. La scheda ignorata non viene mostrata o salvata. Una risposta aziendale con giudizi non validi non salva derivati: viene registrata come errore e si passa alla prossima azienda senza ritentare la stessa richiesta. La run termina parziale se resta almeno un errore. Errori HTTP o di trasporto continuano a fermare la run.
