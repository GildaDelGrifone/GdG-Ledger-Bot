# AGENTS.md: context for AI coding assistants

Baseline for working on **GdG-Ledger-Bot**. Read this before changing code; keep it up to date when flows, sheet columns or commands change (see "Maintenance").

User-facing strings, docstrings and code comments in this project are in **Italian**. Keep them in Italian. This file is in English only because it is meant for tools.

## What this is
A Telegram bot that keeps an accounting ledger in Google Sheets for a small association. It tracks two cash boxes (🔴 Rossa = main, 🔵 Blu = change/float), records income/expenses, and links Satispay and SumUp payments to sheet rows via background polling.

Stack: Python 3.10+, python-telegram-bot ≥ 21 (with `JobQueue`), gspread, SQLite (stdlib), httpx, pydantic / pydantic-settings, PyYAML, qrcode. Tests: pytest + pytest-asyncio.

## Commands
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # or: uv venv && uv pip install -r requirements.txt
.venv/bin/python -m pytest -q          # full test suite, no network or credentials needed
.venv/bin/python main.py               # run the bot (or ./run.sh)
```
`USE_MOCK_SHEETS=true` runs against an in-memory sheet. Configuration lives in `.env` (see `.env.example`); never commit it, nor `credentials.json`, `*.pem` or `*.db`.

## Layout
- `main.py`: builds the Telegram app, registers handlers, schedules the polling jobs (`poll_satispay_job`, `poll_sumup_job`; SumUp only if `SUMUP_API_KEY` is set).
- `config.py`: `settings` (pydantic-settings, reads `.env`).
- `bot/conversation.py`: the `ConversationHandler` for `/write`, `/transfer` and the YAML macros.
- `bot/handlers.py`: stateless commands (`/start /help /cassa /link /unlink /sat_* /sumup_*`) and the `format_*_echo` notification texts.
- `bot/keyboards.py`: inline keyboards and `CALLBACK_*` constants. `bot/custom_commands.py`: YAML macro loader + `setMyCommands` list.
- `services/sheets_service.py`: `SheetsServiceInterface`, `MockSheetsService`, `GoogleSheetsService` (+ layout migration).
- `services/db_service.py`: SQLite (`satispay_payments`, `sumup_transactions`). `services/satispay_service.py`, `services/sumup_service.py`: API clients and polling.
- `core/models.py` (Pydantic models, `Transaction.to_sheet_row()` / `sheet_headers()`), `core/security.py` (`restricted`, `is_chat_allowed`), `core/qr.py`.
- `custom_commands/*.yaml`: macros with `defaults`. `tests/`: see "Testing".

## The `/write` flow (read this before touching conversation code)
`advance_or_finish()` in `bot/conversation.py` is the **single dispatcher**. Each step runs only if its key is missing from `context.user_data["tx_data"]`, asks the question and returns a `STATE_*`; every handler stores its answer and calls `advance_or_finish()` again. Order:

1. `date_time` → 2. `method` (Contanti / Satispay / SumUp) → 2b. `transfer_direction` (only when `kind == "transfer"`) → 3. `box_red` + `box_blue` (confirm current balances) → 4. `description` → 5. `flow` → 6. `amount` → 7. `box_red_updated` + `box_blue_updated` → 8. `receipt_number` (skipped for transfers) → 9. Satispay QR choice (`qr_choice_done`) → 9b. SumUp choice (`sumup_choice_done`) → `finalize_and_save_transaction()`.

To add a question: add a step with its own key, a `STATE_*` (extend the `range(N)` tuple), a handler, and register the state in `build_conversation_handler`. Macros prefill keys in `make_custom_command_starter`, so prefilled steps are skipped automatically.

Other conversation facts:
- `STATE_QR_WAITING` and `STATE_SUMUP_WAITING` both use `handle_qr_waiting_input`; the background wait tasks stop when `qr_skipped` / `qr_cancelled` is set in `user_data`. `finalize_and_save_transaction` is guarded by `is_finalized`, and also forces the conversation state to END because it may be called from a background task.
- `/transfer` (`start_transfer`) presets `kind`, `date_time`, `method="Trasferimento"`, `flow="Trasferimento"`, `receipt_number=0`; the description comes from the chosen direction.

## Google Sheet layout (16 columns)
`A ID | B Data e Ora | C Metodo | D Cassa Rossa Iniziale | E Cassa Blu Iniziale | F Descrizione | G Flusso | H Importo | I Cassa Rossa Aggiornata | J Cassa Blu Aggiornata | K Numero Ricevuta | L Operatore | M Telegram ID | N ID Satispay | O Data Registrazione | P ID SumUp`

- Column positions are centralised as constants in `services/sheets_service.py` (`COL_RED_AFTER=8`, `COL_BLUE_AFTER=9`, `COL_RECEIPT=10`, `SATISPAY_COLUMN_INDEX=13`, `SUMUP_COLUMN_INDEX=15`, 0-based). **Never hard-code indexes.**
- `Transaction.to_sheet_row()` and `Transaction.sheet_headers()` must stay in sync with each other and with those constants.
- `GoogleSheetsService.ensure_headers()` migrates old sheets automatically (single-cash layout → two boxes, then adds "ID SumUp"). Any future layout change needs the same kind of non-destructive migration, plus Mock and test coverage.
- Dates are always `DD/MM/YYYY HH:MM:SS` (`format_datetime_for_sheet`).
- Sheets I/O is blocking: call it through `asyncio.to_thread`. Reads are cached for 10 s and invalidated on writes.

## Cash box invariants
- Cash (`Contanti`) transactions use the Blu box by default; the "updated balance" step has a button to use the Rossa instead (`cash_box` = `"blue"` / `"red"`, also settable in macro `defaults`). Method stays `Contanti`; the box used is visible in the balance columns.
- Satispay and SumUp transactions leave both balances unchanged (updated = initial).
- Transfers are rows with method and flow `Trasferimento`, no receipt, and cannot exceed the source box balance.
- Current balances = last non-empty values of columns I and J (`get_last_box_balances`, each box searched independently).

## Electronic payments
Shared pattern: `JobQueue` polls the provider → new items are stored in SQLite → a Telegram notification is sent. The first run on an empty table syncs silently (no notification flood); later runs also catch up payments received while the bot was off.

- **Satispay**: signed requests (RSA, HTTP Signatures), dynamic QR via `create_payment`; the stored ID is the payment id.
- **SumUp**: stored ID is the `transaction_code`. In `/write` the bot either waits for a terminal payment or shows a hosted-checkout QR (`SUMUP_QR_ENABLED=false` hides the QR option). It links the **first new successful transaction after the wait started and does not compare amounts** (`find_new_payment`); the sheet amount is the one typed in the bot. A "skip" button always lets the user save the row without an ID.
- The SumUp **3G+ terminal cannot be driven by the Cloud API** (that is Solo only), and no API for printing custom receipts on it was found.
- `/link` and `/unlink` work on replies to notifications: `extract_payment_ref()` detects the provider from the message text (Telegram's `reply_to.text` is plain text, without HTML tags).

## Code conventions
- Telegram messages use `parse_mode="HTML"`; escape any dynamic text with `html.escape`.
- Answer callback queries immediately (`ack_callback_query`). Handlers of restricted commands use `@restricted`; the conversation entry points check `is_chat_allowed`.
- Prefer extending existing helpers over adding new ones; do not add dependencies without a reason.
- Keep `.env.example`, `README.md` and `.GEMINI.md` aligned with new settings.

## Testing
- Use `MockSheetsService` (see `tests/conftest.py`), temporary SQLite files (`DatabaseService(tmp.name)`), `AsyncMock` for API calls, and the `_make_update` helper in `tests/test_bot_logic.py` for conversation tests.
- Trap: `test_is_chat_allowed` sets `settings.TELEGRAM_ALLOWED_CHAT_ID` globally and never resets it. Tests that go through `is_chat_allowed` must take the chat id from `settings`, not assume `0`.
- Run `pytest -q` before committing. Never hit real Telegram, Google, Satispay or SumUp from tests.

## Git conventions
- Work on a feature branch, not directly on `main`; `main` receives changes through pull requests.
- Commit as the repository's configured local identity (`Destard20`); do not use any other account or email.
- Do not add AI `Co-Authored-By` trailers to commits or generated footers to PRs.
- Do not push, merge or open PRs unless explicitly asked.

## Maintenance
When you change a flow, the sheet columns, the commands, the settings or an invariant above, update this file and the README in the same change.
