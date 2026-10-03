import html
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict
import httpx
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes
from core.security import restricted
from core.models import CustomCommandConfig, SatispayPayment
from services.sheets_service import get_sheets_service
from services.db_service import db_service
from services.satispay_service import satispay_service

logger = logging.getLogger(__name__)

SATISPAY_ID_REGEX = re.compile(
    r"(?:ID Satispay:\s*`?([a-zA-Z0-9_\-]+)`?)|(?:`([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})`)"
)


def format_satispay_echo(payment: SatispayPayment) -> str:
    """Formatta il messaggio di notifica Satispay identico per polling e rievocazione /sat_get"""
    flow_text = "Ricevuto pagamento da" if payment.flow != "REFUND" else "Rimborso verso"
    amount_sign = "+" if payment.flow != "REFUND" else "-"

    return (
        "🔔 *Notifica Pagamento Satispay*\n\n"
        f"📥 *{flow_text}:* {payment.sender_name or 'Cliente'}\n"
        f"💰 *Importo:* `{amount_sign}{payment.amount_euro:.2f} €`\n"
        f"🆔 *ID Satispay:* `{payment.id}`\n"
        f"📝 *Note:* {payment.comment or 'Nessuna nota'}\n"
        f"⏰ *Data:* {payment.insert_date or 'Adesso'}\n\n"
        "💡 *Per associare questa transazione a una riga del registro, "
        "rispondi a questo messaggio con:*\n`/link <id_transazione>`"
    )


def extract_satispay_id_from_text(text: str) -> Optional[str]:
    """Estrae l'ID transazione Satispay dal testo del messaggio a cui si risponde"""
    match = SATISPAY_ID_REGEX.search(text)
    if match:
        return match.group(1) or match.group(2)
    return None


@restricted
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Messaggio di benvenuto del bot"""
    welcome_text = (
        "👋 *Benvenuto nel Ledger Bot!*\n\n"
        "Questo bot ti permette di registrare entrate, uscite e monitorare la cassa "
        "direttamente su Google Sheets, con supporto ai pagamenti Satispay.\n\n"
        "📌 *Comandi principali:*\n"
        "• `/write` (o `/w`) - Avvia la procedura guidata di inserimento transazione\n"
        "• `/help` - Mostra la guida completa e i comandi personalizzati disponibili\n"
        "• `/cancel` - Annulla un inserimento in corso\n"
    )
    await update.effective_chat.send_message(welcome_text, parse_mode="Markdown")


@restricted
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guida dettagliata all'uso del bot"""
    custom_commands: Dict[str, CustomCommandConfig] = context.bot_data.get("custom_commands", {})

    custom_text = ""
    if custom_commands:
        custom_text = "\n⚡ *Macro e comandi veloci disponibili:*\n"
        for cmd_name, cfg in custom_commands.items():
            custom_text += f"• `/{cmd_name}` - {cfg.description}\n"

    help_text = (
        "📖 *Guida all'uso del Ledger Bot*\n\n"
        "🔹 *Registrazione Transazioni:*\n"
        "Usa il comando `/write` (scorciatoia `/w`) per avviare una procedura interattiva.\n"
        "Il bot ti guiderà passo dopo passo ponendo domande su data, metodo (Contanti o Satispay), "
        "saldo cassa, descrizione, flusso (entrata/uscita), importo e ricevuta.\n"
        f"{custom_text}\n"
        "🔹 *Integrazione Satispay & Collegamento:*\n"
        "Il bot controlla periodicamente i pagamenti Satispay e invia una notifica in chat.\n"
        "• `/link <id_transazione>`: Rispondi al messaggio Satispay per associare quell'ID alla riga nel foglio.\n"
        "• `/unlink`: Rispondi per rimuovere l'ID Satispay da tutte le righe del foglio.\n"
        "• `/unlink <id_transazione>`: Rispondi per scollegarlo solo da una riga specifica.\n\n"
        "🔹 *Storico Satispay:*\n"
        "• `/sat_list [data]`: Mostra le transazioni Satispay della data (es: `/sat_list 30/09/2026` o oggi se omessa).\n"
        "• `/sat_list_range <da> <a>`: Mostra le transazioni in un intervallo (es: `/sat_list_range 01/09/2026 30/09/2026`).\n"
        "• `/sat_get <ID_Satispay>`: Richiama la notifica di un pagamento per poter usare `/link` in qualsiasi momento.\n"
    )
    await update.effective_chat.send_message(help_text, parse_mode="Markdown")
@restricted
async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Collega un ID Satispay a una riga di transazione del foglio.
    Sintassi: rispondi a una notifica Satispay con /link <id_transazione>
    """
    reply_to = update.message.reply_to_message if update.message else None
    if not reply_to or not reply_to.text:
        await update.effective_chat.send_message(
            "⚠️ *Istruzioni:* Per collegare un pagamento Satispay, rispondi (reply) "
            "al messaggio di notifica Satispay scrivendo:\n`/link <id_transazione>` (es: `/link 12`)",
            parse_mode="Markdown",
        )
        return

    satispay_id = extract_satispay_id_from_text(reply_to.text)
    if not satispay_id:
        await update.effective_chat.send_message(
            "❌ Impossibile rilevare un ID Satispay nel messaggio a cui hai risposto. "
            "Assicurati di rispondere alla notifica di pagamento inviata dal bot.",
            parse_mode="Markdown",
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_chat.send_message(
            "⚠️ Specifica l'ID numerico della transazione del foglio da collegare (es: `/link 15`).",
            parse_mode="Markdown",
        )
        return

    tx_id = int(context.args[0])
    user = update.effective_user
    username = user.username or user.first_name if user else "Anonimo"
    user_id = user.id if user else 0
    logger.info(f"Utente @{username} (ID: {user_id}) ha richiesto collegamento transazione #{tx_id} a Satispay ID: {satispay_id}")
    sheets = get_sheets_service()

    success = sheets.link_satispay_id(tx_id, satispay_id)
    if success:
        await update.effective_chat.send_message(
            f"✅ Transazione `#{tx_id}` collegata con successo all'ID Satispay:\n`{satispay_id}`",
            parse_mode="Markdown",
        )
    else:
        await update.effective_chat.send_message(
            f"❌ Transazione `#{tx_id}` non trovata nel foglio di calcolo.",
            parse_mode="Markdown",
        )


@restricted
async def unlink_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Scollega un ID Satispay dal foglio.
    Sintassi: rispondi a una notifica Satispay con /unlink oppure /unlink <id_transazione>
    """
    reply_to = update.message.reply_to_message if update.message else None
    if not reply_to or not reply_to.text:
        await update.effective_chat.send_message(
            "⚠️ *Istruzioni:* Per scollegare un pagamento Satispay, rispondi (reply) "
            "al messaggio di notifica Satispay scrivendo:\n`/unlink` oppure `/unlink <id_transazione>`",
            parse_mode="Markdown",
        )
        return

    satispay_id = extract_satispay_id_from_text(reply_to.text)
    if not satispay_id:
        await update.effective_chat.send_message(
            "❌ Impossibile rilevare un ID Satispay nel messaggio a cui hai risposto.",
            parse_mode="Markdown",
        )
        return

    tx_id: Optional[int] = None
    if context.args and context.args[0].isdigit():
        tx_id = int(context.args[0])

    user = update.effective_user
    username = user.username or user.first_name if user else "Anonimo"
    user_id = user.id if user else 0
    target_descr = f"transazione #{tx_id}" if tx_id is not None else "tutte le transazioni"
    logger.info(f"Utente @{username} (ID: {user_id}) ha richiesto scollegamento Satispay ID: {satispay_id} da {target_descr}")

    sheets = get_sheets_service()
    count = sheets.unlink_satispay_id(satispay_id, transaction_id=tx_id)

    if count > 0:
        target_str = f"dalla transazione #{tx_id}" if tx_id is not None else "da tutte le transazioni collegate"
        await update.effective_chat.send_message(
            f"✅ ID Satispay `{satispay_id}` scollegato con successo {target_str} (righe aggiornate: {count}).",
            parse_mode="Markdown",
        )
    else:
        await update.effective_chat.send_message(
            f"ℹ️ Nessuna corrispondenza trovata nel foglio per l'ID Satispay `{satispay_id}`.",
            parse_mode="Markdown",
        )


@restricted
async def sat_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra l'elenco delle transazioni Satispay per una data specifica (o oggi se omessa).
    Se non ci sono transazioni nella data, mostra le ultime registrate nel database.
    Uso: /sat_list [GG/MM/AAAA]
    """
    has_arg = bool(context.args)
    date_arg = context.args[0] if has_arg else datetime.now().strftime("%d/%m/%Y")
    payments = db_service.get_payments_by_date(date_arg)
    total_stored = db_service.count_payments()

    if not payments:
        recent = db_service.get_recent_payments(limit=5)
        if not recent:
            await update.effective_chat.send_message(
                f"ℹ️ Il database locale Satispay è attualmente vuoto (nessun pagamento rilevato).",
                parse_mode="Markdown"
            )
            return

        date_desc = f"per la data `{date_arg}`" if has_arg else f"per oggi (`{date_arg}`)"
        lines = [
            f"ℹ️ Nessun pagamento Satispay registrato {date_desc}.\n",
            f"📋 *Ultime transazioni nel database (Totale salvate: {total_stored}):*\n"
        ]
        for idx, p in enumerate(recent, start=1):
            sign = "+" if p.flow != "REFUND" else "-"
            dt_display = p.insert_date[:16].replace("T", " ") if p.insert_date else ""
            lines.append(
                f"{idx}. `{p.id}`\n"
                f"   💰 *{sign}{p.amount_euro:.2f} €* | 👤 {p.sender_name or 'Anonimo'} | 📅 {dt_display}\n"
            )
        lines.append(
            "💡 *Cerca per data specifica:* `/sat_list <GG/MM/AAAA>`\n"
            "💡 *Oppure per intervallo:* `/sat_list_range <da> <a>`\n"
            "💡 *Per richiamare la notifica di un pagamento e collegarlo:* `/sat_get <ID>`"
        )
        await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown")
        return

    lines = [f"📋 *Transazioni Satispay del {date_arg} ({len(payments)} trovate):*\n"]
    for idx, p in enumerate(payments, start=1):
        sign = "+" if p.flow != "REFUND" else "-"
        time_part = p.insert_date.split("T")[-1][:5] if "T" in (p.insert_date or "") else (p.insert_date or "")[-8:-3]
        lines.append(
            f"{idx}. `{p.id}`\n"
            f"   💰 *{sign}{p.amount_euro:.2f} €* | 👤 {p.sender_name or 'Anonimo'} ({time_part})\n"
        )

    lines.append("💡 *Per richiamare la notifica di un pagamento e collegarlo con /link, usa:*\n`/sat_get <ID_Satispay>`")
    await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown")


@restricted
async def sat_list_range_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra l'elenco delle transazioni Satispay in un intervallo di date.
    Uso: /sat_list_range <data_inizio> <data_fine>
    Esempio: /sat_list_range 01/09/2026 30/09/2026
    """
    if not context.args or len(context.args) < 2:
        await update.effective_chat.send_message(
            "⚠️ *Uso del comando:* `/sat_list_range <data_inizio> <data_fine>`\n"
            "Esempio: `/sat_list_range 01/09/2026 30/09/2026`",
            parse_mode="Markdown"
        )
        return

    start_date = context.args[0]
    end_date = context.args[1]
    payments = db_service.get_payments_by_range(start_date, end_date)

    if not payments:
        await update.effective_chat.send_message(
            f"ℹ️ Nessun pagamento Satispay registrato tra `{start_date}` e `{end_date}`.",
            parse_mode="Markdown"
        )
        return

    lines = [f"📋 *Transazioni Satispay dal {start_date} al {end_date} ({len(payments)} trovate):*\n"]
    for idx, p in enumerate(payments[:50], start=1):
        sign = "+" if p.flow != "REFUND" else "-"
        date_str = p.insert_date[:16].replace("T", " ") if p.insert_date else ""
        lines.append(
            f"{idx}. `{p.id}`\n"
            f"   💰 *{sign}{p.amount_euro:.2f} €* | 👤 {p.sender_name or 'Anonimo'} | 📅 {date_str}\n"
        )

    if len(payments) > 50:
        lines.append(f"_...e altre {len(payments) - 50} transazioni. Riduci l'intervallo per vederle tutte._\n")

    lines.append("💡 *Per richiamare la notifica di un pagamento e collegarlo con /link, usa:*\n`/sat_get <ID_Satispay>`")
    await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown")


@restricted
async def sat_get_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Rievoca il messaggio di notifica ufficiale di una transazione Satispay dato il suo ID,
    permettendo di rispondere direttamente con /link <id_transazione>.
    Uso: /sat_get <ID_Satispay>
    """
    if not context.args:
        await update.effective_chat.send_message(
            "⚠️ *Uso del comando:* `/sat_get <ID_Satispay>`\n"
            "Esempio: `/sat_get 55rdsjb6o99...`",
            parse_mode="Markdown"
        )
        return

    target_id = context.args[0].strip("`").strip()
    payment = db_service.get_payment_by_id(target_id)

    # Fallback alle API Satispay se non trovato localmente nel database
    if not payment:
        payment = await satispay_service.get_payment(target_id)

    if not payment:
        await update.effective_chat.send_message(
            f"❌ Nessuna transazione Satispay trovata con ID:\n`{target_id}`",
            parse_mode="Markdown"
        )
        return

    echo_msg = format_satispay_echo(payment)
    await update.effective_chat.send_message(echo_msg, parse_mode="Markdown")



async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log degli errori imprevisti"""
    # Gestione attenuata per errori di rete o timeout temporanei (es. httpx.ReadError durante getUpdates)
    if isinstance(context.error, (NetworkError, TimedOut, httpx.HTTPError)):
        logger.warning(
            "Problema di rete temporaneo durante la comunicazione con Telegram: %s",
            context.error,
        )
        return

    logger.error("Eccezione durante la gestione dell'aggiornamento:", exc_info=context.error)

