# Documentazione

Questo indice è l'ingresso unico alla documentazione corrente. I report di esperimenti, le proposte superate e la vecchia guida HTML sono stati rimossi. Restano disponibili nella cronologia Git.

## Capire il sistema

- [Panoramica illustrata](jobhunter-overview.html) presenta il funzionamento ad alto livello, con diagrammi e numeri dell'archivio; è in inglese, con un pulsante per l'italiano.
- [DESIGN.md](../DESIGN.md) definisce le decisioni di prodotto e gli invarianti della pipeline.
- [PRODUCT.md](../PRODUCT.md) descrive l'esperienza utente e il significato delle viste.
- [Mappa del codice e dei dati](mappa-codice-dati.md) indica dove vive ogni responsabilità.
- [Modello dati](data-model.md) documenta tabelle, campi persistenti e dati derivati.

## Usare JobHunter

- [Uso e manutenzione](jobhunter-v2.md) raccoglie avvio, CLI, backup e verifiche.
- [Pipeline completa](pipeline-completa.md) segue un annuncio e un'azienda lungo tutti i passaggi.
- [Pannello Pipeline](pipeline-ui.md) spiega avvio, stato, stop e ripresa dalla dashboard.
- [Metriche](metrics.md) descrive conteggi, popolazioni e limiti della pagina.
- [Conversazioni Codex](conversazioni-codex.md) separa revisione degli indecisi, esplorazione e Salvate.

## Integrare e configurare

- [Configurazione](configuration.md) elenca i file attivi e cosa invalida le cache.
- [Fonti dello scraper](scraper-sources.md) spiega i tipi di fonte e come aggiungerne una.
- [Jev](system-one.md) documenta il secondo giudice automatico.
- [Schede via API](remote-llm.md) documenta Qwen, validazione, costi e cache.
- [Onboarding](jobhunter-onboarding.md) descrive come preparare una copia per un'altra persona.

## Regola di manutenzione

Aggiorna il documento che possiede l'argomento. Non aggiungere un secondo report per descrivere lo stesso comportamento. Un esperimento temporaneo appartiene ai dati di lavoro o alla cronologia Git, non all'indice operativo.
