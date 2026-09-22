# Jev in JobHunter

Jev è il secondo e ultimo giudice automatico. Riceve soltanto i ruoli che il regex lascia
indecisi. Decide se tenerli e, quando l'azienda non ha ancora un settore corrente, la categorizza
nella stessa richiesta. Qwen resta responsabile delle schede in prosa.

La documentazione TypeSafe è la fonte per il contratto dell'API:

- [API HTTP](https://docs.typesafe.ai/api.md)
- [primitive Choice, Noul e Score](https://docs.typesafe.ai/primitives.md)
- [domande parallele](https://docs.typesafe.ai/cookbooks/parallel_questions.md)
- [confidence e soglie](https://docs.typesafe.ai/confidence.md)

## Perché scelta e categoria viaggiano insieme

TypeSafe valuta le domande della stessa richiesta in modo indipendente. Nessuna risposta diventa
contesto nascosto per le altre. Se il ruolo e l'azienda condividono lo stesso testo, due richieste
ripeterebbero stato, latenza e token in ingresso.

JobHunter costruisce quindi tre forme di richiesta:

| Lavoro pendente | Contenuto della richiesta |
|---|---|
| ruolo e azienda | sei domande sul ruolo, una per ogni settore e due cancelli aziendali |
| solo ruolo | sei domande sul ruolo |
| solo azienda | una domanda per ogni settore e due cancelli aziendali |

I risultati restano separati. Il ruolo viene salvato come `jev:selection` sull'annuncio; il settore
come `jev:category` sull'azienda. Ogni risultato ha la propria impronta di stato, domande, soglie e
modello. L'impronta della categoria comprende anche il vocabolario corrente: aggiungere o cambiare
un settore rimette in coda soltanto le aziende interessate. Una modifica ai dati aziendali non
invalida un giudizio sul ruolo già corrente.

Ogni asse ha anche una `decision_version`. Va incrementata quando cambia il codice che combina le
risposte, così una nuova regola non riusa un verdetto calcolato con la logica precedente.

Se lo stato combinato supera `max_state_chars`, JobHunter divide il lavoro e lo segnala come
`split_oversized`. Non tronca testo in silenzio.

## Configurazione

`config/system_one.json` usa l'endpoint ufficiale:

```text
https://api.typesafe.ai/v1/systemone
```

Il modello configurato è `jev-latest`. La risposta salva anche l'identificatore versionato che ha
servito la richiesta, utile quando si calibrano le soglie.

### Parallelismo e rate limit

Le richieste indipendenti viaggiano in parallelo; il valore predefinito è 64 worker e la CLI
accetta `--workers N`. I worker eseguono soltanto il trasporto HTTP. Preparazione, validazione e
scritture SQLite restano sul thread principale, così una singola connessione al database non viene
condivisa fra thread.

Le partenze sono distribuite nel tempo e rispettano `max_requests_per_minute`, anche quando ci sono
molti worker liberi. Il valore `0` disabilita esplicitamente questo pacing; è la configurazione
corrente, scelta dall'utente dopo che le prove a 60 e 500/minuto non hanno prodotto throttling. Non
esiste comunque una quota pubblicata o garantita da TypeSafe. Un
`429` o `529` viene ritentato con attesa crescente; un errore di trasporto,
per cui la fatturazione potrebbe essere già avvenuta, ferma invece nuove partenze. Aumentare i
worker riduce l'attesa solo finché la latenza delle chiamate è il collo di bottiglia: non supera il
tetto di richieste al minuto e non riduce il costo.

La chiave vive soltanto in `.env.local`, ignorato da Git. Il campo è già presente:

```dotenv
TYPESAFE_API_KEY=
```

Incolla la chiave dopo `=` senza virgolette e senza spazi. Non inserirla in
`config/system_one.json`, `.env.local.example`, schermate o report.

## Primo test

L'anteprima prepara lo stesso campione ma non legge la chiave e non chiama TypeSafe:

```powershell
python main.py system-one --limit 3
```

Controlla nel JSON:

- `total`: richieste previste;
- `counts.combined`: richieste che faranno entrambe le valutazioni;
- `counts.selection_only` e `counts.category_only`: richieste con un solo gruppo di domande;
- `items[].tasks`: lavoro incluso in ogni richiesta;
- `state_chars`: dimensione dello stato inviato.

Dopo aver inserito la chiave, il primo test reale è:

```powershell
python main.py system-one --limit 3 --execute
```

Il comando salva i risultati, il modello effettivo e l'uso token. Ripeterlo non ripaga gli stessi
input correnti: la cache esclude i risultati già validi. Dalla dashboard lo stesso flusso si chiama
"Scegli e categorizza con Jev"; la modalità iniziale è sempre Anteprima.

Per un pilot più prudente si può lavorare su una copia dell'archivio:

```powershell
Copy-Item data\jobhunter.sqlite3 data\jobhunter-jev-pilot.sqlite3
python main.py --db data\jobhunter-jev-pilot.sqlite3 system-one --limit 3 --execute
```

## Domande e regole

Le domande sono in `config/system-one-questions.json`. Jev riceve uno stato e una mappa di domande,
non un prompt di chat.

Per il ruolo:

- `mansioni_descritte`, Noul;
- `mansioni_compatibili`, Noul;
- `famiglia_esclusa`, Choice;
- `posto_per_studenti`, Noul;
- `seniority_fuori_profilo`, Noul;
- `prova`, Choice sulle frasi reali dell'annuncio.

Per l'azienda:

- `settori`, espansa in una domanda Noul indipendente per ogni voce di `config/categories.json`;
- `descrive_il_datore`, Noul;
- `agenzia`, Noul.

`judgement()` e `classification()` applicano le soglie di `config/system_one.json`. Jev non fa
aritmetica e non sceglie il flusso. Se il testo non descrive mansioni concrete, il ruolo va in
`review` con l'informazione mancante invece di diventare un falso scarto. Una famiglia esclusa e
una compatibilità almeno pari a 0,60 producono `review` per segnali in conflitto. I ruoli fisici
inequivocabili, limitati a produzione, installazione, manutenzione, officina e ricambi, possono
invece essere esclusi anche da una descrizione breve quando la compatibilità non supera 0,20. La
domanda sulla seniority copre titoli senior o manageriali ed esperienza obbligatoria oltre due
anni, anche in annunci non inglesi.

Un datore che opera come
agenzia, staffing o recruiting viene classificato in `Consulenza e servizi`; non eredita il settore
del cliente. Il codice conserva al massimo due categorie: punteggio individuale minimo 0,40,
categoria singola da 0,60, oppure coppia con somma almeno 0,80. La somma è una soglia operativa,
non una probabilità composta. Una categoria scelta in chat non viene mai sovrascritta.

Choice e Score riportano `confidence`; Noul riporta direttamente la probabilità del sì. Le soglie
attuali sono valori iniziali. Vanno calibrate su un campione reale, soprattutto sugli annunci in
italiano. Un output tipizzato impedisce valori fuori schema, non garantisce che il giudizio sia
corretto.

Le soglie restano iniziali: vanno ricontrollate su campioni reali con
`scripts/audit_jev_results.py`, senza nuove chiamate né consumo di token.

Per ricontrollare un batch senza chiamare modelli né usare token, lo script seguente sceglie casi
di soglia e un campione deterministico, poi verifica le invarianti del combinatore:

```powershell
python scripts\audit_jev_results.py --per-decision 5 --category-sample 10
```

Senza `--report` usa automaticamente l'ultimo report Jev.

## Errori e ritenti

JobHunter ritenta 429 e 5xx, incluso 529, con attesa crescente. Un errore di trasporto ha un esito
di fatturazione incerto: il passaggio si ferma e non ritenta automaticamente. Ogni richiesta usa
HTTPS e un bearer token; non esistono più stub, gateway alternativi o percorsi senza credenziale.
