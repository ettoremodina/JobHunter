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
| ruolo e azienda | quattro domande sul ruolo e tre sull'azienda |
| solo ruolo | quattro domande sul ruolo |
| solo azienda | tre domande sull'azienda |

I risultati restano separati. Il ruolo viene salvato come `jev:selection` sull'annuncio; il settore
come `jev:category` sull'azienda. Ogni risultato ha la propria impronta di stato, domande, soglie e
modello. Una modifica ai dati aziendali non invalida un giudizio sul ruolo già corrente.

Se lo stato combinato supera `max_state_chars`, JobHunter divide il lavoro e lo segnala come
`split_oversized`. Non tronca testo in silenzio.

## Configurazione

`config/system_one.json` usa l'endpoint ufficiale:

```text
https://api.typesafe.ai/v1/systemone
```

Il modello configurato è `jev-latest`. La risposta salva anche l'identificatore versionato che ha
servito la richiesta, utile quando si calibrano le soglie.

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

- `mansioni_compatibili`, Noul;
- `famiglia_esclusa`, Choice;
- `posto_per_studenti`, Noul;
- `prova`, Choice sulle frasi reali dell'annuncio.

Per l'azienda:

- `categoria`, Choice sul vocabolario di `config/categories.json`;
- `descrive_il_datore`, Noul;
- `agenzia`, Noul.

`judgement()` e `classification()` applicano le soglie di `config/system_one.json`. Jev non fa
aritmetica e non sceglie il flusso. Una categoria scelta in chat non viene mai sovrascritta.

Choice e Score riportano `confidence`; Noul riporta direttamente la probabilità del sì. Le soglie
attuali sono valori iniziali. Vanno calibrate su un campione reale, soprattutto sugli annunci in
italiano. Un output tipizzato impedisce valori fuori schema, non garantisce che il giudizio sia
corretto.

## Errori e ritenti

JobHunter ritenta 429 e 5xx, incluso 529, con attesa crescente. Un errore di trasporto ha un esito
di fatturazione incerto: il passaggio si ferma e non ritenta automaticamente. Ogni richiesta usa
HTTPS e un bearer token; non esistono più stub, gateway alternativi o percorsi senza credenziale.
