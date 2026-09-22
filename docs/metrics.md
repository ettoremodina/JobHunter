# Metriche dell'archivio

La tab Metriche e `python main.py analytics` usano `jobhunter/exploration/analytics.py`. Il calcolo legge l'archivio corrente e i filtri locali, senza rete o modelli. Può aggiornare soltanto cache locali derivate e la data dell'ultimo calcolo. `--eligibility potential`, `review` o `excluded` limita la sezione di qualità e composizione; i totali della pipeline restano riferiti all'intero archivio.

La pagina è divisa in tre letture distinte:

1. **Stato di elaborazione.** Per raccolta, descrizioni, regex e Jev mostra input, completati, da elaborare e bloccati dai dati. Ogni riga dichiara il proprio denominatore.
2. **Esito della compatibilità.** Un albero mostra che la Regex può solo scartare; il ramo non scartato arriva a Jev, che può tenere, scartare o mandare alla revisione. Il riepilogo finale comprende soltanto annunci già decisi e dichiara quanti restano senza esito Jev. Gli annunci bloccati perché privi di mansioni utilizzabili non entrano nel totale degli esiti.
3. **Dagli annunci compatibili alle aziende.** Un ponte indica quanti annunci compatibili appartengono a quante aziende distinte. Le aziende vengono subito ripartite per tier e la somma coincide con il totale del ponte. Tier B attesa non compare, perché per definizione non ha un annuncio compatibile.
4. **Mappa di tutte le aziende.** Il conteggio riparte esplicitamente dall'intero archivio aziendale. Qui compaiono anche Tier B attesa, aziende senza categoria e aziende fuori selezione. Non è una tappa successiva del ponte precedente. "Senza categoria" riguarda l'asse azienda e non deriva da uno scarto Regex sugli annunci: indica che nessun settore è stato assegnato per dati insufficienti o valutazione non conclusa.

Le distribuzioni di qualità, categoria e geografia usano invece il numero di annunci univoci nella selezione. Le categorie sono settori aziendali ereditati dagli annunci. Un'assegnazione automatica non equivale a una validazione umana.

La geografia riconosce nomi e codici espliciti definiti in `config/geography.json`, senza geocoding. Le città isolate, le sigle ambigue e i testi non riconosciuti restano non determinati. Il dizionario è estendibile; non è una copertura geografica completa. Per paesi transcontinentali è prevista una voce Europa / Asia. Un annuncio con più località conta una sola volta per ciascun paese e continente; la somma delle percentuali può superare il 100%. Una località riconosciuta non prova l'idoneità al lavoro remoto. Il filtro testuale per città nella tab Aziende rimane disponibile.

La salute misura presenza di descrizione, categoria, paese riconosciuto, salario e data di pubblicazione. Non certifica qualità del testo, correttezza del salario o disponibilità attuale del ruolo. In particolare, una descrizione solo formalmente presente può comunque richiedere pulizia.

L'asse azienda usa prima le regole sulle evidenze aziendali e poi Jev per i casi rimasti senza categoria. Jev può valutare la categoria anche senza un annuncio compatibile: le richieste solo-categoria impediscono al verdetto Regex sul ruolo di nascondere l'azienda.

La tab effettua una sola richiesta a `/api/analytics?eligibility=potential`: non aspetta più il riepilogo operativo di `/api/pipeline`. La disponibilità delle mansioni viene letta dal verdetto locale salvato; non si ripete il parsing HTML per ogni annuncio a ogni apertura. Dopo una modifica alle regole, il primo calcolo può aggiornare la cache dei filtri; le letture successive la riusano.

Per aggiungere un indicatore aggiornare `summary`, il relativo rendering nella dashboard e i test dei conteggi. `/api/metrics` conserva le metriche del feedback già usate dalla coda.
