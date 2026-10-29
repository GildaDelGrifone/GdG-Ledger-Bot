# 📊 GdG-Ledger-Bot

Telegram Bot professionale per la tenuta del registro contabile (Ledger), gestione della cassa contanti e sincronizzazione automatica su **Google Sheets**, con supporto nativo alle notifiche e collegamento dei pagamenti **Satispay Business**.

---

## ✨ Funzionalità Principali

* **Sicurezza Chat Autorizzata**: Il bot risponde unicamente all'ID chat configurato nel file `.env`, ignorando ed isolando ogni comando proveniente da altre chat o utenti non autorizzati.
* **Tag Esplicito dell'Utente nei Gruppi**: Il bot antepone a ogni domanda il tag diretto dell'utente (`👤 @username` o menzione diretta Telegram). In questo modo, anche se più membri interagiscono contemporaneamente nello stesso gruppo, è sempre evidente a colpo d'occhio chi deve rispondere.
* **Procedura Guidata `/write` (shortcut `/w`)**:
  * Richiede sequenzialmente: Data e Ora, Metodo (Contanti, Satispay o SumUp), conferma dei saldi di 🔴 Cassa Rossa e 🔵 Cassa Blu, Descrizione, Flusso (Entrata/Uscita), Importo, Cassa Blu aggiornata (solo se contanti, altrimenti i saldi restano invariati) e Numero ricevuta.
  * Interfaccia ottimizzata con **pulsanti interattivi (Inline Buttons)**:
    * Pulsante per impostare l'orario attuale (`/now`).
    * Pulsante per confermare gli ultimi saldi di Cassa Rossa e Cassa Blu registrati su Google Sheets (oppure inserirli a mano nel formato `Rossa Blu`, es. `120 35,50`).
    * Calcolo automatico del saldo aggiornato della Cassa Blu (Saldo Iniziale ± Importo per i contanti; per Satispay e SumUp i saldi restano invariati) con pulsante di conferma rapida o inserimento manuale.
    * Suggerimento progressivo per il numero di ricevuta (Ultimo registrato + 1).
  * Registra l'username e l'ID Telegram dell'operatore per audit e sicurezza.
  * Invia un riepilogo formattato in chat alla conclusione dell'inserimento.
* **Doppia Cassa Contanti (🔴 Rossa / 🔵 Blu)**:
  * **Cassa Rossa**: cassa principale. **Cassa Blu**: piccola cassa per il resto e per custodire l'incasso finché l'eccedenza non viene versata nella Rossa. Le transazioni in contanti usano la Cassa Blu per default; al passaggio del saldo aggiornato il pulsante **🔴 Usa Cassa Rossa** (e **🔵 Usa Cassa Blu** per tornare indietro) permette di usare direttamente la Rossa, senza passare da `/transfer`. Le macro YAML possono impostare `cash_box: red` nei `defaults` per puntare sempre alla Rossa.
  * Ogni riga del foglio registra i saldi iniziali e aggiornati di **entrambe** le casse (colonne D/E e I/J).
  * `/cassa` (alias `/saldo`): mostra i saldi attuali di Rossa e Blu e il totale contanti.
  * `/transfer` (alias `/tr`, `/trasferimento`): sposta contanti scegliendo la direzione (**Blu → Rossa** per versare l'eccedenza, **Rossa → Blu** per ricaricare il resto). Chiede conferma dei saldi e l'importo (che non può superare il saldo della cassa di origine) e registra una riga con metodo e flusso `Trasferimento`, senza ricevuta.
  * **Migrazione automatica**: al primo avvio con un foglio nel vecchio layout (cassa unica) il bot inserisce le nuove colonne Blu. I vecchi saldi restano nella Cassa Rossa e la Blu parte da zero (puoi correggere i saldi al primo `/write`). **Fai una copia del foglio prima del primo avvio.**
* **Archiviazione Cloud su Google Sheets**:
  * Struttura a colonne ordinata con autoincremento progressivo dell'ID transazione.
  * Formato date standardizzato: tutte le colonne temporali (*Data e Ora* e *Data Registrazione*) sono salvate in formato `GG/MM/AAAA HH:MM:SS` (`DD/MM/YYYY HH:MM:SS`).
  * Auto-sanitizzazione dell'ID: supporta sia l'ID pulito che l'URL completo di Google Drive.
  * Supporto a fogli multipli nella stessa cartella di lavoro (tab specificabile da `.env`).
  * Modalità Mock locale (`USE_MOCK_SHEETS=true`) per test immediati anche senza credenziali Google configurate.
  * **Prestazioni e Reattività Elevate**: Caching intelligente della cartella di lavoro e dei dati (TTL 10s con invalidazione immediata in scrittura), esecuzione asincrona in thread separati (`asyncio.to_thread`) e risposta immediata alle callback dei pulsanti per eliminare qualsiasi tempo di attesa o spinner di caricamento.
* **Integrazione Satispay Business 100% Autonoma (Polling + SQLite)**:
  * **Nessun Webhook, porta aperta o Cloudflare Tunnel**: il bot interroga periodicamente le API di Satispay in background tramite `JobQueue` interna.
  * **Persistenza Locale su SQLite (`satispay_history.db`)**: memorizza i pagamenti ricevuti. Al riavvio del bot, recupera automaticamente qualsiasi transazione avvenuta a PC spento (Offline Catch-up) senza duplicare notifiche.
  * **Comandi Storico Satispay**:
    * `/sat_list [data]` (alias `/sl`): visualizza l'elenco delle transazioni di una data (es: `/sat_list 30/09/2026` o oggi se omessa).
    * `/sat_list_range <da> <a>` (alias `/slr`): visualizza le transazioni in un intervallo di date (es: `/sat_list_range 01/09/2026 30/09/2026`).
    * `/sat_get <ID_Satispay>` (alias `/sg`): rievoca la notifica ufficiale di una transazione, permettendo di collegarla con `/link` in qualsiasi momento.
  * **Associazione e Scollegamento**:
    * `/link <id_transazione>`: rispondi a un messaggio Satispay per associare l'ID Satispay alla riga corrispondente nel foglio di calcolo.
    * `/unlink`: rispondi per rimuovere l'ID Satispay da tutte le righe del foglio (o `/unlink <id_transazione>`).
* **POS Virtuale con QR Code Dinamico**:
  * Se il metodo scelto è **Satispay** ed è un'**Entrata (+)**, prima di chiudere la transazione il bot chiede se si vuole generare un QR Code Satispay.
  * Invia l'immagine del codice QR con l'importo esatto: quando il cliente autorizza il pagamento sull'app, il bot lo rileva in tempo reale, completa la transazione e associa l'ID Satispay su Google Sheets in automatico!
  * È sempre disponibile il pulsante per saltare l'attesa del pagamento e procedere comunque con la registrazione manuale sul foglio.

* **Integrazione SumUp (Polling + SQLite)**:
  * Il bot interroga periodicamente lo storico transazioni SumUp (`SUMUP_API_KEY`), le salva in SQLite e notifica i nuovi incassi in chat, con Offline Catch-up come per Satispay.
  * **Metodo SumUp in `/write`**: per le entrate il bot può attendere il pagamento sul terminale (**SumUp 3G+**) oppure generare un **QR con link di pagamento**. Il bot collega la prima transazione SumUp completata senza verificarne l'importo (sul foglio resta quello inserito) e il pulsante **Salta pagamento e registra** permette di proseguire senza ID SumUp (disattivabile con `SUMUP_QR_ENABLED=false`). A pagamento avvenuto il codice transazione viene scritto nella colonna P `ID SumUp` del foglio.
  * **Comandi**: `/sumup_list [data]` (`/sul`), `/sumup_list_range <da> <a>` (`/sulr`), `/sumup_get <codice>` (`/sug`). `/link` e `/unlink` funzionano anche sulle notifiche SumUp.

* **Macro e Comandi Personalizzati Dinamici**:
  * Cartella dedicata `custom_commands/`: aggiungi file `.yaml` per creare macro rapide (es. `/i`, `/bb`).
  * Registrazione automatica all'avvio nel menu dei comandi Telegram tramite `setMyCommands`.
  * Supporto a valori predefiniti (`defaults`): data, metodo, flusso, importo fisso, prefissi o suffissi per la causale con visualizzazione preventiva all'utente. I campi già impostati vengono saltati automaticamente.
* **Logging di Sessione & Spegnimento Pulito**:
  * Generazione automatica di file di log dedicati in `logs/` con timestamp di avvio (es. `logs/bot_YYYYMMDD_HHMMSS.log`).
  * Tracciamento dettagliato su file e console di ogni operazione di scrittura sul foglio di calcolo.
  * Arresto pulito e nativo gestito da `python-telegram-bot` (`Ctrl+C`).

---

## 📁 Struttura del Progetto

```text
GdG-Ledger-Bot/
├── bot/
│   ├── conversation.py        # Flusso interattivo /write e gestione comandi custom
│   ├── custom_commands.py     # Loader file YAML e generatore comandi Telegram
│   ├── handlers.py            # /start, /help, /link, /unlink, /sat_list, /sat_get
│   └── keyboards.py           # Inline Keyboards e bottoni interattivi
├── core/
│   ├── models.py              # Modelli dati Pydantic (Transaction, CustomCommand, SatispayPayment, SumUpTransaction)
│   ├── qr.py                  # Generazione immagini QR code
│   └── security.py            # Decoratori e filtri di autorizzazione chat
├── services/
│   ├── db_service.py          # Database SQLite locale per storico pagamenti Satispay
│   ├── sheets_service.py      # Servizio Google Sheets (gspread) e Mock locale
│   ├── satispay_service.py    # Client Satispay con HTTP Signatures e Polling
│   └── sumup_service.py       # Client SumUp: storico, polling, abbinamento importo, checkout
├── custom_commands/           # Cartella con le definizioni YAML dei comandi custom
│   ├── iscrizione.yaml
│   └── iscrizione_bayblade.yaml
├── logs/                      # Cartella file di log di sessione (generata all'avvio)
├── tests/                     # Suite di test unitari con Pytest
│   ├── conftest.py
│   ├── test_bot_logic.py
│   ├── test_custom_commands.py
│   ├── test_satispay_service.py
│   ├── test_sumup_service.py
│   └── test_sheets_service.py
├── .env.example               # Template variabili d'ambiente (senza segreti)
├── .GEMINI.md                 # Guida dettagliata per ottenere credenziali API Google e Satispay
├── main.py                    # Entry point: avvia Telegram Bot e JobQueue Satispay Polling
├── requirements.txt           # Dipendenze Python
└── satispay_setup.py          # Script interattivo per generazione chiavi RSA e scambio KeyID
```
---

## 🚀 Installazione e Avvio Rapido

### 1. Prerequisiti
* Python 3.10 o superiore.
* Un token bot ottenuto da [@BotFather](https://t.me/BotFather) su Telegram.

### 2. Configurazione Ambiente Virtuale
```bash
# Crea e attiva l'ambiente virtuale
python3 -m venv .venv
source .venv/bin/activate

# Installa le dipendenze
pip install -r requirements.txt
```

### 3. Configurazione Variabili d'Ambiente (.env)
Copia il file di esempio `.env.example` in `.env`:
```bash
cp .env.example .env
```
Modifica `.env` con i tuoi valori:
* `TELEGRAM_BOT_TOKEN`: Il token del bot fornito da BotFather.
* `TELEGRAM_ALLOWED_CHAT_ID`: L'ID numerico della chat o gruppo abilitato.
* `USE_MOCK_SHEETS`: Lascia `true` per provare subito il bot in memoria locale senza credenziali Google, oppure `false` una volta configurato Google Sheets.
* Per la guida completa all'ottenimento delle credenziali Google Sheets e Satispay, consulta il file [`.GEMINI.md`](.GEMINI.md), che descrive anche le variabili `SUMUP_*` (tra cui `SUMUP_QR_ENABLED` per disattivare il QR SumUp).

### 4. Esecuzione dei Test
Puoi verificare l'integrità dell'intero progetto eseguendo:
```bash
pytest -v
```

### 5. Avvio del Bot
```bash
python main.py
```
Il bot è ora attivo e completamente autonomo! Non serve avviare nessun tunnel, porta o server web esterno: il polling periodico di Satispay e la gestione dei comandi Telegram sono interamente orchestrati dal processo principale.

---

## ⚙️ Creazione di Comandi Personalizzati (Macro)

Per creare un nuovo comando, crea un file `.yaml` all'interno della cartella `custom_commands/` (es. `custom_commands/pizza.yaml`):

```yaml
command: "pizza"
description: "Registra pizza di gruppo (10€ Contanti)"
defaults:
  date_time: "now"          # Imposta automaticamente data e ora corrente
  method: "Contanti"        # Pre-seleziona Contanti
  flow: "Uscita"            # Uscita fondi
  amount: 10.00             # Importo fisso
  description: "Pizza sociale"
```

È anche possibile omettere `description` e impostare un prefisso o suffisso (es. `description_prefix: "Iscrizione: "`), in modo che il bot chieda la causale all'utente mostrando l'anteprima e applicando automaticamente il prefisso!

Al prossimo riavvio, il bot registrerà automaticamente i comandi su Telegram. Quando un utente digiterà la macro, il bot salterà le domande per cui è già presente un default e chiederà unicamente i dati mancanti!

---

## 📄 Licenza
Progetto rilasciato ad uso interno sotto licenza MIT.

