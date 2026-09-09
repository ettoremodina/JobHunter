# Modello dati

Complemento a [`DESIGN.md`](../DESIGN.md). Qui ci sono i campi, uno per uno: cosa
contengono, chi li scrive, cosa succede quando mancano.

Deciso il 9 settembre 2026.

## Principio

**L'annuncio contiene solo il ruolo. L'azienda contiene solo l'azienda.**

Prima della revisione ogni annuncio si portava dentro una copia dei dati aziendali
(nome, descrizione, settore, sito), che esistevano anche nella tabella aziende. Due
copie della stessa cosa, che potevano non essere d'accordo: vinceva l'ultimo annuncio
importato.

Unica eccezione: il link alla pagina dell'azienda **su quella board** resta
sull'annuncio, perché cambia da fonte a fonte. La stessa azienda ha una pagina su
LinkedIn e una diversa su Indeed.

---

## Azienda

Tabella `companies`.

| campo | tipo | chi lo scrive |
|---|---|---|
| `id` | testo | derivato: nome normalizzato + host del sito |
| `name` | testo | la fonte |
| `name_key` | testo | normalizzazione del nome, serve al raggruppamento |
| `website` | URL | la fonte, **oppure `hiringOrganization.sameAs`** letto durante il recupero descrizioni |
| `description` | testo | la cascata di estrazione (§5 di DESIGN.md), **non** la fonte |
| `description_provenance` | oggetto | quale leva ha prodotto il testo e quando |
| `sectors` | testo | la fonte |
| `first_seen`, `last_seen` | data ISO | osservazione |

`description_provenance` dice **da dove viene il «chi siamo»**, perché le quattro
leve non hanno la stessa affidabilità:

```json
{
  "method": "listing | intersection | about_heading | local_llm",
  "extracted_at": "2026-09-09T10:00:00+00:00",
  "job_ids": ["..."]
}
```

`job_ids` è valorizzato solo per `intersection`: sono gli annunci il cui testo comune
ha prodotto la descrizione. Serve a rifare il calcolo quando arrivano annunci nuovi.

La **categoria** dell'azienda vive nella tabella `categories` (`category`, `method`,
`reason`, `updated_at`) e non si duplica qui. È il verdetto dell'asse azienda.

## Annuncio

Tabella `opportunities`, campo `data` in JSON.

| campo | tipo | chi lo scrive |
|---|---|---|
| `title` | testo | la fonte |
| `description` | testo | il recupero descrizioni, **ripulito dal boilerplate aziendale** |
| `description_provenance` | oggetto | URL, metodo di estrazione, quando, file salvato |
| `locations` | lista di testo | la fonte |
| `remote_policy` | testo o null | la fonte |
| `employment_type` | testo o null | la fonte |
| `posted_at` | data ISO o null | la fonte |
| `salary` | oggetto | la fonte |
| `source_url` | URL | la fonte |
| `application_url` | URL | la fonte; è anche la chiave d'identità |
| `company_profile_url` | URL | la fonte — pagina azienda **su quella board** |
| `source` | testo | quale fonte ha prodotto la riga |

`salary` ha sempre la stessa forma, anche quando è vuoto:

```json
{"min": null, "max": null, "currency": null, "period": null, "raw_text": null}
```

`first_seen` e `last_seen` sono colonne della tabella, non campi del JSON.

### Campi rimossi

| campo | perché |
|---|---|
| `company_name` | resta un campo **in ingresso**, usato da `normalize()` per risolvere l'azienda, ma non viene più salvato nel record |
| `company_description` | è dell'azienda |
| `sectors` | è dell'azienda |
| `website_url` | è dell'azienda |
| `eligible_countries` | nessuna fonte lo produce |
| `seniority` | nessuna fonte lo produce; la seniority si legge dal titolo e dal testo |

### Il testo del ruolo è ripulito

Quando l'estrazione trova il boilerplate aziendale (§5 di DESIGN.md), quel testo va
in `companies.description` e viene **sottratto** da `opportunities.description`.

Due motivi. I prompt smettono di ripetere la stessa presentazione aziendale a ogni
chiamata, che su un prompt di poche migliaia di token è una fetta grossa moltiplicata
per ogni annuncio. E il giudizio sul ruolo smette di dipendere da quanto è ben scritta
la pagina «about».

## Verdetti e provenienza

**Nessuna tabella nuova.** I verdetti stanno dove stanno già, e la catena delle
motivazioni si assembla in lettura (§7 di DESIGN.md).

| giudice | asse | dove scrive |
|---|---|---|
| regex | ruolo | `search_eligibility.decision` — `status` più `reasons` |
| LLM locale | ruolo | `enrichments`, task `local:selection` |
| LLM locale | azienda | `categories`, `method = 'local_llm'` |
| LLM remoto | ruolo | `enrichments`, task `remote:selection` |
| LLM remoto | azienda | `categories`, `method = 'remote'` |
| utente | entrambi | `feedback` + `feedback_detail`, con la nota come commento sulla scheda |

Il **tier non si salva mai**: si calcola leggendo i due verdetti d'asse.

## Regole di parzialità

**Nessun passo blocca i successivi perché il proprio lavoro è incompleto.**

Ogni passo restituisce uno di tre stati, e vanno tenuti distinti:

| stato | significa | la sequenza |
|---|---|---|
| `success` | tutto fatto | prosegue |
| `partial` | alcuni record non si sono potuti elaborare | **prosegue** |
| `failed` | il passo si è rotto: credenziali, trasporto, database | si ferma |

`partial` è lo stato normale e permanente, non un'anomalia: alcune pagine hanno un
formato che il tool non sa leggere e non lo saprà mai. Trattarlo come un errore
significa non arrivare mai in fondo.

Ne discendono tre regole concrete:

1. **Le aziende che hanno già l'evidenza proseguono** ai passi successivi, senza
   aspettare quelle che non ce l'hanno.
2. Le aziende senza evidenza restano nello stato `evidenza mancante`, che è una coda
   di lavoro e non un rifiuto (§2 di DESIGN.md).
3. Quando l'evidenza arriva, quelle aziende vengono **rigiudicate**, non ignorate.
   Il tier si ricalcola a ogni lettura, quindi succede da solo.

## Migrazione

Cambiare la forma di `opportunities.data` cambia il `content_hash` di ogni annuncio.
Questo invalida `search_eligibility` e **tutte le cache dei giudizi LLM**.

Va fatto **una volta sola e prima** della prima passata completa. Farlo dopo
significa pagare due volte gli stessi giudizi.
