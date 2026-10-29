import asyncio
import html
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Tuple
import httpx
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes
from core.security import restricted
from core.models import CustomCommandConfig, SatispayPayment, SumUpTransaction
from services.sheets_service import get_sheets_service
from services.db_service import db_service
from services.satispay_service import satispay_service
from services.sumup_service import sumup_service

logger = logging.getLogger(__name__)

SATISPAY_ID_REGEX = re.compile(
    r"(?:ID Satispay:\s*(?:<code>|`)?([a-zA-Z0-9_\-]+)(?:</code>|`)?)|(?:(?:<code>|`)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:</code>|`)?)"
)

SUMUP_ID_REGEX = re.compile(r"ID SumUp:\s*(?:<code>|`)?([A-Za-z0-9]+)(?:</code>|`)?")


def format_satispay_echo(payment: SatispayPayment) -> str:
    """Formatta il messaggio di notifica Satispay identico per polling e rievocazione /sat_get"""
    flow_text = "Ricevuto pagamento da" if payment.flow != "REFUND" else "Rimborso verso"
    amount_sign = "+" if payment.flow != "REFUND" else "-"

    sender = html.escape(payment.sender_name or 'Cliente')
    p_id = html.escape(payment.id)
    comment = html.escape(payment.comment or 'Nessuna nota')
    insert_date = html.escape(payment.insert_date or 'Adesso')

    return (
        "🔔 <b>Notifica Pagamento Satispay</b>\n\n"
        f"📥 <b>{flow_text}:</b> {sender}\n"
        f"💰 <b>Importo:</b> <code>{amount_sign}{payment.amount_euro:.2f} €</code>\n"
        f"🆔 <b>ID Satispay:</b> <code>{p_id}</code>\n"
        f"📝 <b>Note:</b> {comment}\n"
        f"⏰ <b>Data:</b> {insert_date}\n\n"
        "💡 <b>Per associare questa transazione a una riga del registro, "
        "rispondi a questo messaggio con:</b>\n<code>/link &lt;id_transazione&gt;</code>"
    )


def format_sumup_echo(tx: SumUpTransaction) -> str:
    """Formatta il messaggio di notifica SumUp identico per polling e rievocazione /sumup_get"""
    is_refund = (tx.type or "").upper() == "REFUND"
    flow_text = "Rimborso" if is_refund else "Ricevuto pagamento"
    amount_sign = "-" if is_refund else "+"
    card = html.escape(" ".join(filter(None, [tx.card_type, tx.payment_type])) or "N/D")

    return (
        "🔔 <b>Notifica Pagamento SumUp</b>\n\n"
        f"📥 <b>{flow_text}</b>\n"
        f"💰 <b>Importo:</b> <code>{amount_sign}{tx.amount_euro:.2f} €</code>\n"
        f"💳 <b>Carta/Tipo:</b> {card}\n"
        f"🆔 <b>ID SumUp:</b> <code>{html.escape(tx.transaction_code)}</code>\n"
        f"⏰ <b>Data:</b> {html.escape(tx.timestamp or 'Adesso')}\n\n"
        "💡 <b>Per associare questa transazione a una riga del registro, "
        "rispondi a questo messaggio con:</b>\n<code>/link &lt;id_transazione&gt;</code>"
    )


def extract_payment_ref(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Estrae (provider, id) dal messaggio a cui si risponde: 'sumup', 'satispay' oppure (None, None)"""
    match = SUMUP_ID_REGEX.search(text)
    if match:
        return "sumup", match.group(1)
    sat_id = extract_satispay_id_from_text(text)
    if sat_id:
        return "satispay", sat_id
    return None, None


def extract_satispay_id_from_text(text: str) -> Optional[str]:
    """Estrae l'ID transazione Satispay dal testo del messaggio a cui si risponde"""
    match = SATISPAY_ID_REGEX.search(text)
    if match:
        return match.group(1) or match.group(2)
    return None


@restricted
async def cassa_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra gli ultimi saldi registrati di Cassa Rossa e Cassa Blu"""
    sheets = get_sheets_service()
    red, blue = await asyncio.to_thread(sheets.get_last_box_balances)
    await update.effective_chat.send_message(
        "🪙 <b>Saldi attuali delle casse</b>\n\n"
        f"🔴 <b>Cassa Rossa:</b> <code>{red:.2f} €</code>\n"
        f"🔵 <b>Cassa Blu:</b> <code>{blue:.2f} €</code>\n"
        f"💰 <b>Totale contanti:</b> <code>{red + blue:.2f} €</code>\n\n"
        "💡 Per spostare contanti tra le casse usa <code>/transfer</code>.",
        parse_mode="HTML",
    )


@restricted
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Messaggio di benvenuto del bot"""
    welcome_text = (
        "👋 <b>Benvenuto nel Ledger Bot!</b>\n\n"
        "Questo bot ti permette di registrare entrate, uscite e monitorare la cassa "
        "direttamente su Google Sheets, con supporto ai pagamenti Satispay.\n\n"
        "📌 <b>Comandi principali:</b>\n"
        "• <code>/write</code> (o <code>/w</code>) - Avvia la procedura guidata di inserimento transazione\n"
        "• <code>/help</code> - Mostra la guida completa e i comandi personalizzati disponibili\n"
        "• <code>/transfer</code> (o <code>/tr</code>) - Sposta contanti tra Cassa Rossa e Cassa Blu\n"
        "• <code>/cassa</code> - Mostra i saldi di Cassa Rossa e Cassa Blu\n"
        "• <code>/cancel</code> - Annulla un inserimento in corso\n"
    )
    await update.effective_chat.send_message(welcome_text, parse_mode="HTML")


@restricted
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guida dettagliata all'uso del bot"""
    custom_commands: Dict[str, CustomCommandConfig] = context.bot_data.get("custom_commands", {})

    custom_text = ""
    if custom_commands:
        custom_text = "\n⚡ <b>Macro e comandi veloci disponibili:</b>\n"
        for cmd_name, cfg in custom_commands.items():
            desc_esc = html.escape(cfg.description)
            custom_text += f"• <code>/{cmd_name}</code> - {desc_esc}\n"

    help_text = (
        "📖 <b>Guida all'uso del Ledger Bot</b>\n\n"
        "🔹 <b>Registrazione Transazioni:</b>\n"
        "Usa il comando <code>/write</code> (scorciatoia <code>/w</code>) per avviare una procedura interattiva.\n"
        "Il bot ti guiderà passo dopo passo ponendo domande su data, metodo (Contanti, Satispay o SumUp), "
        "saldi delle casse, descrizione, flusso (entrata/uscita), importo e ricevuta.\n"
        "Per le entrate con Satispay o SumUp il bot può attendere il pagamento e collegarlo in automatico.\n"
        f"{custom_text}\n"
        "🔹 <b>Doppia cassa (🔴 Rossa / 🔵 Blu):</b>\n"
        "Il contante è diviso in Cassa Rossa (principale) e Cassa Blu (resto). Le transazioni in contanti usano la Cassa Blu per default (puoi scegliere la Rossa con il pulsante 🔴 al passaggio del saldo aggiornato).\n"
        "• <code>/cassa</code> (o <code>/saldo</code>): Mostra i saldi attuali delle due casse.\n"
        "• <code>/transfer</code> (o <code>/tr</code>): Sposta contanti da Blu a Rossa o da Rossa a Blu.\n\n"
        "🔹 <b>Integrazione Satispay &amp; Collegamento:</b>\n"
        "Il bot controlla periodicamente i pagamenti Satispay e invia una notifica in chat.\n"
        "• <code>/link &lt;id_transazione&gt;</code>: Rispondi al messaggio Satispay per associare quell'ID alla riga nel foglio.\n"
        "• <code>/unlink</code>: Rispondi per rimuovere l'ID Satispay da tutte le righe del foglio.\n"
        "• <code>/unlink &lt;id_transazione&gt;</code>: Rispondi per scollegarlo solo da una riga specifica.\n\n"
        "🔹 <b>Storico Satispay:</b>\n"
        "• <code>/sat_list [data]</code>: Mostra le transazioni Satispay della data (es: <code>/sat_list 30/09/2026</code> o oggi se omessa).\n"
        "• <code>/sat_list_range &lt;da&gt; &lt;a&gt;</code>: Mostra le transazioni in un intervallo (es: <code>/sat_list_range 01/09/2026 30/09/2026</code>).\n"
        "• <code>/sat_get &lt;ID_Satispay&gt;</code>: Richiama la notifica di un pagamento per poter usare <code>/link</code> in qualsiasi momento.\n\n"
        "🔹 <b>Integrazione SumUp:</b>\n"
        "Le transazioni SumUp vengono notificate in chat; <code>/link</code> e <code>/unlink</code> funzionano allo stesso modo (colonna <i>ID SumUp</i>).\n"
        "• <code>/sumup_list [data]</code> (<code>/sul</code>): Transazioni SumUp della data (o di oggi).\n"
        "• <code>/sumup_list_range &lt;da&gt; &lt;a&gt;</code> (<code>/sulr</code>): Transazioni in un intervallo.\n"
        "• <code>/sumup_get &lt;codice&gt;</code> (<code>/sug</code>): Richiama la notifica di una transazione.\n"
    )
    await update.effective_chat.send_message(help_text, parse_mode="HTML")
@restricted
async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Collega un ID Satispay a una riga di transazione del foglio.
    Sintassi: rispondi a una notifica Satispay con /link <id_transazione>
    """
    reply_to = update.message.reply_to_message if update.message else None
    if not reply_to or not reply_to.text:
        await update.effective_chat.send_message(
            "⚠️ <b>Istruzioni:</b> Per collegare un pagamento Satispay, rispondi (reply) "
            "al messaggio di notifica Satispay scrivendo:\n<code>/link &lt;id_transazione&gt;</code> (es: <code>/link 12</code>)",
            parse_mode="HTML",
        )
        return

    provider, satispay_id = extract_payment_ref(reply_to.text)
    if not satispay_id:
        await update.effective_chat.send_message(
            "❌ Impossibile rilevare un ID di pagamento (Satispay o SumUp) nel messaggio a cui hai risposto. "
            "Assicurati di rispondere alla notifica di pagamento inviata dal bot.",
            parse_mode="HTML",
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_chat.send_message(
            "⚠️ Specifica l'ID numerico della transazione del foglio da collegare (es: <code>/link 15</code>).",
            parse_mode="HTML",
        )
        return

    tx_id = int(context.args[0])
    user = update.effective_user
    username = user.username or user.first_name if user else "Anonimo"
    user_id = user.id if user else 0
    logger.info(f"Utente @{username} (ID: {user_id}) ha richiesto collegamento transazione #{tx_id} a {provider} ID: {satispay_id}")
    sheets = get_sheets_service()

    link_fn = sheets.link_sumup_id if provider == "sumup" else sheets.link_satispay_id
    provider_name = "SumUp" if provider == "sumup" else "Satispay"
    success = await asyncio.to_thread(link_fn, tx_id, satispay_id)
    if success:
        await update.effective_chat.send_message(
            f"✅ Transazione <code>#{tx_id}</code> collegata con successo all'ID {provider_name}:\n<code>{html.escape(satispay_id)}</code>",
            parse_mode="HTML",
        )
    else:
        await update.effective_chat.send_message(
            f"❌ Transazione <code>#{tx_id}</code> non trovata nel foglio di calcolo.",
            parse_mode="HTML",
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
            "⚠️ <b>Istruzioni:</b> Per scollegare un pagamento Satispay, rispondi (reply) "
            "al messaggio di notifica Satispay scrivendo:\n<code>/unlink</code> oppure <code>/unlink &lt;id_transazione&gt;</code>",
            parse_mode="HTML",
        )
        return

    provider, satispay_id = extract_payment_ref(reply_to.text)
    if not satispay_id:
        await update.effective_chat.send_message(
            "❌ Impossibile rilevare un ID di pagamento (Satispay o SumUp) nel messaggio a cui hai risposto.",
            parse_mode="HTML",
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
    unlink_fn = sheets.unlink_sumup_id if provider == "sumup" else sheets.unlink_satispay_id
    provider_name = "SumUp" if provider == "sumup" else "Satispay"
    count = await asyncio.to_thread(unlink_fn, satispay_id, transaction_id=tx_id)

    if count > 0:
        target_str = f"dalla transazione #{tx_id}" if tx_id is not None else "da tutte le transazioni collegate"
        await update.effective_chat.send_message(
            f"✅ ID {provider_name} <code>{html.escape(satispay_id)}</code> scollegato con successo {target_str} (righe aggiornate: {count}).",
            parse_mode="HTML",
        )
    else:
        await update.effective_chat.send_message(
            f"ℹ️ Nessuna corrispondenza trovata nel foglio per l'ID {provider_name} <code>{html.escape(satispay_id)}</code>.",
            parse_mode="HTML",
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
                "ℹ️ Il database locale Satispay è attualmente vuoto (nessun pagamento rilevato).",
                parse_mode="HTML"
            )
            return

        date_desc = f"per la data <code>{html.escape(date_arg)}</code>" if has_arg else f"per oggi (<code>{html.escape(date_arg)}</code>)"
        lines = [
            f"ℹ️ Nessun pagamento Satispay registrato {date_desc}.\n",
            f"📋 <b>Ultime transazioni nel database (Totale salvate: {total_stored}):</b>\n"
        ]
        for idx, p in enumerate(recent, start=1):
            sign = "+" if p.flow != "REFUND" else "-"
            dt_display = p.insert_date[:16].replace("T", " ") if p.insert_date else ""
            p_id = html.escape(p.id)
            sender = html.escape(p.sender_name or 'Anonimo')
            lines.append(
                f"{idx}. <code>{p_id}</code>\n"
                f"   💰 <b>{sign}{p.amount_euro:.2f} €</b> | 👤 {sender} | 📅 {dt_display}\n"
            )
        lines.append(
            "💡 <b>Cerca per data specifica:</b> <code>/sat_list &lt;GG/MM/AAAA&gt;</code>\n"
            "💡 <b>Oppure per intervallo:</b> <code>/sat_list_range &lt;da&gt; &lt;a&gt;</code>\n"
            "💡 <b>Per richiamare la notifica di un pagamento e collegarlo:</b> <code>/sat_get &lt;ID&gt;</code>"
        )
        await update.effective_chat.send_message("\n".join(lines), parse_mode="HTML")
        return

    lines = [f"📋 <b>Transazioni Satispay del {html.escape(date_arg)} ({len(payments)} trovate):</b>\n"]
    for idx, p in enumerate(payments, start=1):
        sign = "+" if p.flow != "REFUND" else "-"
        time_part = p.insert_date.split("T")[-1][:5] if "T" in (p.insert_date or "") else (p.insert_date or "")[-8:-3]
        p_id = html.escape(p.id)
        sender = html.escape(p.sender_name or 'Anonimo')
        lines.append(
            f"{idx}. <code>{p_id}</code>\n"
            f"   💰 <b>{sign}{p.amount_euro:.2f} €</b> | 👤 {sender} ({time_part})\n"
        )

    lines.append("💡 <b>Per richiamare la notifica di un pagamento e collegarlo con /link, usa:</b>\n<code>/sat_get &lt;ID_Satispay&gt;</code>")
    await update.effective_chat.send_message("\n".join(lines), parse_mode="HTML")


@restricted
async def sat_list_range_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra l'elenco delle transazioni Satispay in un intervallo di date.
    Uso: /sat_list_range <data_inizio> <data_fine>
    Esempio: /sat_list_range 01/09/2026 30/09/2026
    """
    if not context.args or len(context.args) < 2:
        await update.effective_chat.send_message(
            "⚠️ <b>Uso del comando:</b> <code>/sat_list_range &lt;data_inizio&gt; &lt;data_fine&gt;</code>\n"
            "Esempio: <code>/sat_list_range 01/09/2026 30/09/2026</code>",
            parse_mode="HTML"
        )
        return

    start_date = context.args[0]
    end_date = context.args[1]
    payments = db_service.get_payments_by_range(start_date, end_date)

    if not payments:
        await update.effective_chat.send_message(
            f"ℹ️ Nessun pagamento Satispay registrato tra <code>{html.escape(start_date)}</code> e <code>{html.escape(end_date)}</code>.",
            parse_mode="HTML"
        )
        return

    lines = [f"📋 <b>Transazioni Satispay dal {html.escape(start_date)} al {html.escape(end_date)} ({len(payments)} trovate):</b>\n"]
    for idx, p in enumerate(payments[:50], start=1):
        sign = "+" if p.flow != "REFUND" else "-"
        date_str = p.insert_date[:16].replace("T", " ") if p.insert_date else ""
        p_id = html.escape(p.id)
        sender = html.escape(p.sender_name or 'Anonimo')
        lines.append(
            f"{idx}. <code>{p_id}</code>\n"
            f"   💰 <b>{sign}{p.amount_euro:.2f} €</b> | 👤 {sender} | 📅 {date_str}\n"
        )

    if len(payments) > 50:
        lines.append(f"<i>...e altre {len(payments) - 50} transazioni. Riduci l'intervallo per vederle tutte.</i>\n")

    lines.append("💡 <b>Per richiamare la notifica di un pagamento e collegarlo con /link, usa:</b>\n<code>/sat_get &lt;ID_Satispay&gt;</code>")
    await update.effective_chat.send_message("\n".join(lines), parse_mode="HTML")


@restricted
async def sat_get_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Rievoca il messaggio di notifica ufficiale di una transazione Satispay dato il suo ID,
    permettendo di rispondere direttamente con /link <id_transazione>.
    Uso: /sat_get <ID_Satispay>
    """
    if not context.args:
        await update.effective_chat.send_message(
            "⚠️ <b>Uso del comando:</b> <code>/sat_get &lt;ID_Satispay&gt;</code>\n"
            "Esempio: <code>/sat_get 55rdsjb6o99...</code>",
            parse_mode="HTML"
        )
        return

    target_id = context.args[0].strip("`").strip()
    payment = db_service.get_payment_by_id(target_id)

    # Fallback alle API Satispay se non trovato localmente nel database
    if not payment:
        payment = await satispay_service.get_payment(target_id)

    if not payment:
        await update.effective_chat.send_message(
            f"❌ Nessuna transazione Satispay trovata con ID:\n<code>{html.escape(target_id)}</code>",
            parse_mode="HTML"
        )
        return

    echo_msg = format_satispay_echo(payment)
    await update.effective_chat.send_message(echo_msg, parse_mode="HTML")


def _format_sumup_line(idx: int, t: SumUpTransaction) -> str:
    sign = "-" if (t.type or "").upper() == "REFUND" else "+"
    ts = (t.timestamp or "")[:16].replace("T", " ")
    status = "" if t.status == "SUCCESSFUL" else f" ({html.escape(t.status)})"
    return (
        f"{idx}. <code>{html.escape(t.transaction_code)}</code>\n"
        f"   💰 <b>{sign}{t.amount_euro:.2f} €</b>{status} | 📅 {ts}\n"
    )


@restricted
async def sumup_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra le transazioni SumUp di una data (o di oggi se omessa).
    Uso: /sumup_list [GG/MM/AAAA]
    """
    has_arg = bool(context.args)
    date_arg = context.args[0] if has_arg else datetime.now().strftime("%d/%m/%Y")
    txs = db_service.get_sumup_by_date(date_arg)

    if not txs:
        recent = db_service.get_recent_sumup(limit=5)
        if not recent:
            await update.effective_chat.send_message(
                "ℹ️ Il database locale SumUp è attualmente vuoto (nessuna transazione rilevata).",
                parse_mode="HTML",
            )
            return
        lines = [
            f"ℹ️ Nessuna transazione SumUp per <code>{html.escape(date_arg)}</code>.\n",
            "📋 <b>Ultime transazioni nel database:</b>\n",
        ]
        lines += [_format_sumup_line(i, t) for i, t in enumerate(recent, start=1)]
        lines.append("💡 <code>/sumup_list &lt;GG/MM/AAAA&gt;</code> · <code>/sumup_get &lt;codice&gt;</code>")
        await update.effective_chat.send_message("\n".join(lines), parse_mode="HTML")
        return

    lines = [f"📋 <b>Transazioni SumUp del {html.escape(date_arg)} ({len(txs)} trovate):</b>\n"]
    lines += [_format_sumup_line(i, t) for i, t in enumerate(txs, start=1)]
    lines.append("💡 <b>Per richiamare la notifica e collegarla con /link:</b> <code>/sumup_get &lt;codice&gt;</code>")
    await update.effective_chat.send_message("\n".join(lines), parse_mode="HTML")


@restricted
async def sumup_list_range_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mostra le transazioni SumUp in un intervallo di date.
    Uso: /sumup_list_range <data_inizio> <data_fine>
    """
    if not context.args or len(context.args) < 2:
        await update.effective_chat.send_message(
            "⚠️ <b>Uso del comando:</b> <code>/sumup_list_range &lt;data_inizio&gt; &lt;data_fine&gt;</code>\n"
            "Esempio: <code>/sumup_list_range 01/09/2026 30/09/2026</code>",
            parse_mode="HTML",
        )
        return

    start_date, end_date = context.args[0], context.args[1]
    txs = db_service.get_sumup_by_range(start_date, end_date)
    if not txs:
        await update.effective_chat.send_message(
            f"ℹ️ Nessuna transazione SumUp tra <code>{html.escape(start_date)}</code> e <code>{html.escape(end_date)}</code>.",
            parse_mode="HTML",
        )
        return

    lines = [f"📋 <b>Transazioni SumUp dal {html.escape(start_date)} al {html.escape(end_date)} ({len(txs)} trovate):</b>\n"]
    lines += [_format_sumup_line(i, t) for i, t in enumerate(txs[:50], start=1)]
    if len(txs) > 50:
        lines.append(f"<i>...e altre {len(txs) - 50} transazioni. Riduci l'intervallo per vederle tutte.</i>\n")
    lines.append("💡 <b>Per richiamare la notifica e collegarla con /link:</b> <code>/sumup_get &lt;codice&gt;</code>")
    await update.effective_chat.send_message("\n".join(lines), parse_mode="HTML")


@restricted
async def sumup_get_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Rievoca la notifica di una transazione SumUp dato il suo codice.
    Uso: /sumup_get <codice>
    """
    if not context.args:
        await update.effective_chat.send_message(
            "⚠️ <b>Uso del comando:</b> <code>/sumup_get &lt;codice_transazione&gt;</code>",
            parse_mode="HTML",
        )
        return

    code = context.args[0].strip("`").strip()
    tx = db_service.get_sumup_by_code(code)
    if not tx:
        tx = await sumup_service.get_transaction(code)

    if not tx:
        await update.effective_chat.send_message(
            f"❌ Nessuna transazione SumUp trovata con codice:\n<code>{html.escape(code)}</code>",
            parse_mode="HTML",
        )
        return

    await update.effective_chat.send_message(format_sumup_echo(tx), parse_mode="HTML")


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

