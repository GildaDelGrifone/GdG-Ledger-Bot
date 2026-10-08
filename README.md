# 📊 GdG-Ledger-Bot

Telegram Bot professionale per la tenuta del registro contabile (Ledger), gestione della cassa contanti e sincronizzazione automatica su **Google Sheets**, con supporto nativo alle notifiche e collegamento dei pagamenti **Satispay Business**.

---

## ✨ Funzionalità Principali

* **Sicurezza Chat Autorizzata**: Il bot risponde unicamente all'ID chat configurato nel file `.env`, ignorando ed isolando ogni comando proveniente da altre chat o utenti non autorizzati.
* **Tag Esplicito dell'Utente nei Gruppi**: Il bot antepone a ogni domanda il tag diretto dell'utente (`👤 @username` o menzione diretta Telegram). In questo modo, anche se più membri interagiscono contemporaneamente nello stesso gruppo, è sempre evidente a colpo d'occhio chi deve rispondere.
* **Procedura Guidata `/write` (shortcut `/w`)**:
  * Richiede sequenzialmente: Data e Ora, Metodo (Contanti o Satispay), Cassa iniziale (o conferma saldo cassa attuale per Satispay), Descrizione, Flusso (Entrata/Uscita), Importo, Cassa aggiornata (solo se contanti, altrimenti allineata automaticamente) e Numero ricevuta.
  * Interfaccia ottimizzata con **pulsanti interattivi (Inline Buttons)**:
    * Pulsante per impostare l'orario attuale (`/now`).
    * Pulsante per selezionare l'ultimo saldo cassa registrato su Google Sheets.
    * Calcolo automatico della cassa aggiornata (Saldo Iniziale ± Importo per contanti; saldo confermato per Satispay sia su colonna D che su colonna H) con pulsante di conferma rapida o inserimento manuale.
    * Suggerimento progressivo per il numero di ricevuta (Ultimo registrato + 1).
  * Registra l'username e l'ID Telegram dell'operatore per audit e sicurezza.
  * Invia un riepilogo formattato in chat alla conclusione dell'inserimento.
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
│   ├── models.py              # Modelli dati Pydantic (Transaction, CustomCommand, SatispayPayment)
│   └── security.py            # Decoratori e filtri di autorizzazione chat
├── services/
│   ├── db_service.py          # Database SQLite locale per storico pagamenti Satispay
│   ├── sheets_service.py      # Servizio Google Sheets (gspread) e Mock locale
│   └── satispay_service.py    # Client Satispay con HTTP Signatures e Polling
├── custom_commands/           # Cartella con le definizioni YAML dei comandi custom
│   ├── iscrizione.yaml
│   └── iscrizione_bayblade.yaml
├── logs/                      # Cartella file di log di sessione (generata all'avvio)
├── tests/                     # Suite di test unitari con Pytest
│   ├── conftest.py
│   ├── test_bot_logic.py
│   ├── test_custom_commands.py
│   ├── test_satispay_service.py
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
* Per la guida completa all'ottenimento delle credenziali Google Sheets e Satispay, consulta il file [`.GEMINI.md`](.GEMINI.md).

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

