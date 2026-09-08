# Metriche dell'archivio

La tab Metriche e `python main.py analytics` usano `jobhunter/analytics.py`. Il calcolo legge l'archivio corrente e i filtri locali, senza rete, modelli o modifiche ai dati. `--eligibility potential`, `review` o `excluded` limita la selezione. La dashboard permette lo stesso confronto e un aggiornamento manuale.

Il denominatore è il numero di annunci univoci nella selezione, non il numero di aziende. Le categorie sono settori aziendali ereditati dagli annunci. Un'assegnazione automatica non equivale a una validazione umana. L'esito dei filtri è mostrato separatamente.

La geografia riconosce nomi e codici espliciti definiti in `config/geography.json`, senza geocoding. Le città isolate, le sigle ambigue e i testi non riconosciuti restano non determinati. Il dizionario è estendibile; non è una copertura geografica completa. Per paesi transcontinentali è prevista una voce Europa / Asia. Un annuncio con più località conta una sola volta per ciascun paese e continente; la somma delle percentuali può superare il 100%. Una località riconosciuta non prova l'idoneità al lavoro remoto. Il filtro testuale per città nella tab Aziende rimane disponibile.

La salute misura presenza di descrizione, categoria, paese riconosciuto, salario e data di pubblicazione. Non certifica qualità del testo, correttezza del salario o disponibilità attuale del ruolo. In particolare, una descrizione solo formalmente presente può comunque richiedere pulizia.

La classificazione Ollama è un passaggio esplicito, distinto dal ricalcolo delle metriche: `python main.py enrich category --missing-only --limit 1000`. Questo comando considera solo aziende ancora senza categoria e con evidenze aziendali; preserva le categorie già assegnate. Le aziende prive di evidenze restano da classificare senza consumare chiamate. I risultati validi sono salvati subito e riutilizzati dalla cache; errori e risposte senza una citazione valida non diventano categorie. Il limite dedicato è `max_category_batch` in `config/local_llm.json`. I suggerimenti locali non sono verifiche umane.

Per aggiungere un indicatore aggiornare `summary`, il relativo rendering nella dashboard e i test dei conteggi. L'endpoint è `/api/analytics?eligibility=potential`. `/api/metrics` conserva le metriche del feedback già usate dalla coda.
