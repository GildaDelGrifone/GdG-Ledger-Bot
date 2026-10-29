import asyncio
import html
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from config import settings
from core.qr import generate_qr_code_image
from core.models import Transaction, CustomCommandConfig, format_datetime_for_sheet
from core.security import is_chat_allowed
from services.sheets_service import get_sheets_service
from services.satispay_service import satispay_service
from services.sumup_service import sumup_service
from services.db_service import db_service
from bot.keyboards import (
    get_now_keyboard,
    get_method_keyboard,
    get_box_balances_keyboard,
    get_transfer_direction_keyboard,
    get_flow_keyboard,
    get_box_updated_keyboard,
    get_receipt_keyboard,
    get_cancel_keyboard,
    get_qr_ask_keyboard,
    get_qr_waiting_keyboard,
    get_sumup_ask_keyboard,
    CALLBACK_NOW,
    CALLBACK_METHOD_CASH,
    CALLBACK_METHOD_SATISPAY,
    CALLBACK_METHOD_SUMUP,
    CALLBACK_FLOW_IN,
    CALLBACK_FLOW_OUT,
    CALLBACK_BOX_USE_LAST,
    CALLBACK_BOX_CONFIRM_CALCULATED,
    CALLBACK_RECEIPT_USE_SUGGESTED,
    CALLBACK_RECEIPT_NONE,
    CALLBACK_CANCEL,
    CALLBACK_QR_GENERATE,
    CALLBACK_QR_SKIP,
    CALLBACK_SUMUP_TERMINAL,
    CALLBACK_SUMUP_LINK,
    CALLBACK_TRANSFER_BLUE_TO_RED,
    CALLBACK_TRANSFER_RED_TO_BLUE,
    CALLBACK_CASH_USE_RED,
    CALLBACK_CASH_USE_BLUE,
)

logger = logging.getLogger(__name__)

# Riferimento globale per permettere la chiusura pulita dal task asincrono del QR
active_conv_handler = None

# Stati del ConversationHandler
(
    STATE_DATETIME,
    STATE_METHOD,
    STATE_BOX_BEFORE,
    STATE_DESCRIPTION,
    STATE_FLOW,
    STATE_AMOUNT,
    STATE_BOX_AFTER,
    STATE_RECEIPT,
    STATE_QR_ASK,
    STATE_QR_WAITING,
    STATE_SUMUP_ASK,
    STATE_SUMUP_WAITING,
    STATE_TRANSFER_DIRECTION,
) = range(13)


def get_user_mention(update: Update) -> str:
    """Restituisce il tag esplicito dell'utente (username o menzione Telegram)"""
    user = update.effective_user
    if not user:
        return ""
    if user.username:
        return f"👤 @{html.escape(user.username)}"
    safe_name = html.escape(user.first_name or "Utente")
    return f'👤 <a href="tg://user?id={user.id}">{safe_name}</a>'


def parse_two_amounts(text: str):
    """Interpreta 'Rossa Blu' (es. '120 35,50' o '120/35.5') restituendo (rossa, blu) oppure None"""
    parts = text.replace("€", " ").replace("/", " ").split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0].replace(",", ".")), float(parts[1].replace(",", "."))
    except ValueError:
        return None


async def ack_callback_query(update: Update) -> None:
    """Risponde immediatamente alla callback query per rimuovere lo spinner di caricamento su Telegram"""
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception as e:
            logger.debug(f"Impossibile rispondere alla callback query (forse scaduta): {e}")


async def send_msg(update: Update, text: str, reply_markup=None, tag_user: bool = True):
    """Helper per inviare o modificare un messaggio in base al tipo di update con tag esplicito utente"""
    await ack_callback_query(update)

    user_tag = get_user_mention(update) if tag_user else ""
    if user_tag and not text.startswith(user_tag):
        formatted_text = f"{user_tag}\n{text}"
    else:
        formatted_text = text

    return await update.effective_chat.send_message(
        text=formatted_text, reply_markup=reply_markup, parse_mode="HTML"
    )
async def advance_or_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Verifica quali campi mancano e pone la domanda successiva, oppure completa la registrazione.
    """
    data = context.user_data.setdefault("tx_data", {})
    sheets = get_sheets_service()

    # 1. Data e Ora
    if "date_time" not in data:
        await send_msg(
            update,
            "📅 <b>Data e Ora della transazione:</b>\n"
            "Premi il pulsante per impostare data e ora attuale, oppure invia <code>/now</code> "
            "o scrivi manualmente la data (<code>GG/MM/AAAA HH.MM</code> o <code>AAAA-MM-GG HH.MM</code>).",
            reply_markup=get_now_keyboard(),
        )
        return STATE_DATETIME

    # 2. Metodo (Contanti o Satispay)
    if "method" not in data:
        await send_msg(
            update,
            "💳 <b>Metodo di pagamento:</b>\n"
            "Come è stata effettuata la transazione?",
            reply_markup=get_method_keyboard(),
        )
        return STATE_METHOD

    # 2b. Trasferimento: direzione (Blu → Rossa oppure Rossa → Blu)
    is_transfer = data.get("kind") == "transfer"
    if is_transfer and "transfer_direction" not in data:
        await send_msg(
            update,
            "🔁 <b>Trasferimento di contanti:</b>\n"
            "Da quale cassa a quale cassa vuoi spostare il denaro?",
            reply_markup=get_transfer_direction_keyboard(),
        )
        return STATE_TRANSFER_DIRECTION

    # 3. Conferma saldi attuali di Cassa Rossa e Cassa Blu
    if "box_red" not in data or "box_blue" not in data:
        last_red, last_blue = await asyncio.to_thread(sheets.get_last_box_balances)
        context.user_data["suggested_balances"] = (last_red, last_blue)

        method = data.get("method")
        if is_transfer:
            prompt_title = "🪙 <b>Conferma saldi delle casse prima del trasferimento:</b>\n"
        elif method == "Contanti":
            prompt_title = "🪙 <b>Saldi delle casse (prima della transazione):</b>\n"
        else:
            prompt_title = "🪙 <b>Conferma saldi delle casse attuali:</b>\n"

        if method == "Contanti":
            cash_note = (
                "Il contante di questa transazione entra/esce dalla 🔵 Cassa Blu "
                "(potrai scegliere la 🔴 Rossa al passaggio successivo).\n\n"
            )
        elif is_transfer:
            cash_note = ""
        else:
            cash_note = (
                f"Il pagamento è effettuato tramite {method}: il contante nelle casse non varia, "
                "ma verifica comunque i saldi.\n\n"
            )

        await send_msg(
            update,
            f"{prompt_title}"
            f"Ultimi saldi rilevati nel registro:\n"
            f"🔴 <b>Cassa Rossa:</b> {last_red:.2f} €\n"
            f"🔵 <b>Cassa Blu:</b> {last_blue:.2f} €\n\n"
            f"{cash_note}"
            "Premi il pulsante per confermarli oppure scrivi i due importi nel formato "
            "<code>Rossa Blu</code> (es. <code>120 35,50</code>).",
            reply_markup=get_box_balances_keyboard(last_red, last_blue),
        )
        return STATE_BOX_BEFORE

    # 4. Descrizione
    if "description" not in data:
        prefix = context.user_data.get("desc_prefix")
        suffix = context.user_data.get("desc_suffix")

        extra_info = ""
        info_lines = []
        if prefix:
            info_lines.append(f"• <b>Prefisso:</b> <code>{html.escape(prefix)}</code>")
        if suffix:
            info_lines.append(f"• <b>Suffisso:</b> <code>{html.escape(suffix)}</code>")
        if info_lines:
            extra_info = "\n\n💡 <i>Personalizzazioni comando:</i>\n" + "\n".join(info_lines)

        await send_msg(
            update,
            "📝 <b>Descrizione della transazione:</b>\n"
            f"Inserisci una breve descrizione della spesa o dell'entrata.{extra_info}",
            reply_markup=get_cancel_keyboard(),
        )
        return STATE_DESCRIPTION

    # 5. Flusso (Entrata o Uscita)
    if "flow" not in data:
        await send_msg(
            update,
            "📊 <b>Flusso di cassa:</b>\n"
            "I fondi sono entrati o usciti?",
            reply_markup=get_flow_keyboard(),
        )
        return STATE_FLOW

    # 6. Importo
    if "amount" not in data:
        await send_msg(
            update,
            "💰 <b>Importo:</b>\n"
            "Inserisci la cifra in euro (es. <code>15.50</code> o <code>15,50</code>).",
            reply_markup=get_cancel_keyboard(),
        )
        return STATE_AMOUNT

    # 7. Saldi aggiornati delle casse
    if "box_red_updated" not in data or "box_blue_updated" not in data:
        red_init = data.get("box_red", 0.0)
        blue_init = data.get("box_blue", 0.0)
        amount = data.get("amount", 0.0)
        method = data.get("method")

        if is_transfer:
            if data.get("transfer_direction") == "blue_to_red":
                data["box_red_updated"] = red_init + amount
                data["box_blue_updated"] = blue_init - amount
            else:
                data["box_red_updated"] = red_init - amount
                data["box_blue_updated"] = blue_init + amount
        elif method == "Contanti":
            # Il contante entra/esce dalla Cassa Blu (default) o dalla Rossa se scelta con il pulsante
            box = data.get("cash_box", "blue")
            if box == "red":
                used_init, other_init = red_init, blue_init
                used_label, other_label = "🔴 Cassa Rossa", "🔵 Cassa Blu"
                data["box_blue_updated"] = blue_init
            else:
                used_init, other_init = blue_init, red_init
                used_label, other_label = "🔵 Cassa Blu", "🔴 Cassa Rossa"
                data["box_red_updated"] = red_init
            flow = data.get("flow", "Entrata")
            calc_used = used_init + amount if flow == "Entrata" else used_init - amount
            context.user_data["suggested_box_after"] = calc_used

            sign = "+" if flow == "Entrata" else "-"
            await send_msg(
                update,
                f"<b>{used_label} aggiornata (dopo la transazione):</b>\n"
                f"Saldo calcolato: <b>{calc_used:.2f} €</b> ({used_init:.2f} € {sign} {amount:.2f} €)\n"
                f"{other_label} invariata: {other_init:.2f} €\n\n"
                "Premi il pulsante per confermare il saldo calcolato oppure scrivi il valore reale se differisce.",
                reply_markup=get_box_updated_keyboard(calc_used, box),
            )
            return STATE_BOX_AFTER
        else:
            # Per i metodi digitali (Satispay/SumUp) il contante nelle casse non varia
            data["box_red_updated"] = red_init
            data["box_blue_updated"] = blue_init

    # 8. Numero Ricevuta
    if "receipt_number" not in data and not is_transfer:
        last_receipt = await asyncio.to_thread(sheets.get_last_receipt_number)
        suggested_receipt = last_receipt + 1
        context.user_data["suggested_receipt"] = suggested_receipt
        await send_msg(
            update,
            f"🧾 <b>Numero ricevuta:</b>\n"
            f"Ultima ricevuta registrata: <b>#{last_receipt}</b>\n\n"
            f"Premi il pulsante per confermare la numero <b>#{suggested_receipt}</b> "
            "oppure scrivi il numero manualmente (scrivi <code>0</code> se non applicabile).",
            reply_markup=get_receipt_keyboard(suggested_receipt),
        )
        return STATE_RECEIPT

    # 9. Se Metodo è Satispay ed è un'Entrata (+), chiedi se si vuole generare un QR Code Satispay POS
    if (
        data.get("method") == "Satispay"
        and data.get("flow") == "Entrata"
        and "qr_choice_done" not in data
    ):
        amount = data.get("amount", 0.0)
        await send_msg(
            update,
            f"📱 <b>Pagamento Satispay POS</b>\n"
            f"Vuoi generare un QR Code per far pagare all'istante l'importo di <b>{amount:.2f} €</b>?\n\n"
            "• Se premi <b>📱 Genera QR Code</b>, verrà mostrato il codice QR e la transazione verrà registrata e collegata in automatico a pagamento avvenuto.\n"
            "• Se premi <b>⏩ Salta e registra subito</b>, la transazione verrà registrata subito nel foglio senza attendere il pagamento.",
            reply_markup=get_qr_ask_keyboard(),
        )
        return STATE_QR_ASK

    # 9b. Se Metodo è SumUp ed è un'Entrata (+), chiedi come incassare (terminale o link/QR)
    if (
        data.get("method") == "SumUp"
        and data.get("flow") == "Entrata"
        and "sumup_choice_done" not in data
    ):
        amount = data.get("amount", 0.0)
        qr_enabled = settings.SUMUP_QR_ENABLED
        link_line = (
            "• Se premi <b>🔗 Genera QR link di pagamento</b>, verrà mostrato un QR code con la pagina di pagamento SumUp.\n"
            if qr_enabled else ""
        )
        await send_msg(
            update,
            f"💳 <b>Pagamento SumUp</b>\n"
            f"Come vuoi incassare <b>{amount:.2f} €</b>?\n\n"
            "• Se premi <b>💳 Attendi pagamento su terminale</b>, esegui il pagamento sul SumUp 3G+: il bot collegherà la prima transazione completata (l'importo non viene verificato).\n"
            f"{link_line}"
            "• Se premi <b>⏩ Salta e registra subito</b>, la transazione verrà registrata subito nel foglio senza attendere il pagamento.",
            reply_markup=get_sumup_ask_keyboard(qr_enabled),
        )
        return STATE_SUMUP_ASK

    return await finalize_and_save_transaction(update, context)


async def finalize_and_save_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Registra la transazione sul foglio di calcolo, invia il messaggio di riepilogo e chiude la conversazione"""
    if context.user_data.get("is_finalized"):
        return ConversationHandler.END
    context.user_data["is_finalized"] = True

    data = context.user_data.get("tx_data", {})
    if not data or "date_time" not in data:
        return ConversationHandler.END

    sheets = get_sheets_service()
    user = update.effective_user
    username = user.username or user.full_name or "Anonimo"
    user_id = user.id

    # Per i metodi digitali, i saldi aggiornati coincidono con quelli confermati
    if data.get("method") in ("Satispay", "SumUp"):
        for key in ("red", "blue"):
            if data.get(f"box_{key}_updated") is None and data.get(f"box_{key}") is not None:
                data[f"box_{key}_updated"] = data[f"box_{key}"]
            elif data.get(f"box_{key}") is None and data.get(f"box_{key}_updated") is not None:
                data[f"box_{key}"] = data[f"box_{key}_updated"]

    tx = Transaction(
        date_time=data["date_time"],
        method=data["method"],
        box_red=data.get("box_red"),
        box_blue=data.get("box_blue"),
        description=data["description"],
        flow=data["flow"],
        amount=data["amount"],
        box_red_updated=data.get("box_red_updated"),
        box_blue_updated=data.get("box_blue_updated"),
        receipt_number=data.get("receipt_number", 0),
        telegram_username=username,
        telegram_user_id=user_id,
        satispay_id=data.get("satispay_id"),
        sumup_id=data.get("sumup_id"),
    )

    try:
        registered_tx = await asyncio.to_thread(sheets.add_transaction, tx)
    except Exception as e:
        logger.error(f"Errore durante salvataggio su foglio: {e}")
        await send_msg(
            update,
            f"❌ <b>Errore durante il salvataggio sul foglio di calcolo:</b>\n<code>{html.escape(str(e))}</code>"
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Messaggio riepilogo inviato in chat
    def _fmt(value) -> str:
        return f"{value:.2f} €" if value is not None else "-"

    cash_box_line = ""
    if registered_tx.method == "Contanti":
        used = "🔴 Rossa" if data.get("cash_box") == "red" else "🔵 Blu"
        cash_box_line = f"💵 <b>Cassa usata:</b> {used}\n"

    box_info = cash_box_line + (
        f"🔴 <b>Cassa Rossa:</b> <code>{_fmt(registered_tx.box_red)} → {_fmt(registered_tx.box_red_updated)}</code>\n"
        f"🔵 <b>Cassa Blu:</b> <code>{_fmt(registered_tx.box_blue)} → {_fmt(registered_tx.box_blue_updated)}</code>\n"
    )

    sat_info = ""
    if registered_tx.satispay_id:
        sat_info = f"📱 <b>ID Satispay Collegato:</b> <code>{html.escape(registered_tx.satispay_id)}</code>\n"

    if registered_tx.sumup_id:
        sat_info += f"💳 <b>ID SumUp Collegato:</b> <code>{html.escape(registered_tx.sumup_id)}</code>\n"

    is_transfer_tx = registered_tx.method == "Trasferimento"
    flow_emoji = {"Entrata": "➕", "Uscita": "➖"}.get(registered_tx.flow, "🔁")
    method_emoji = {"Contanti": "💵", "SumUp": "💳", "Trasferimento": "🔁"}.get(registered_tx.method, "📱")
    title = "✅ <b>Trasferimento Registrato con Successo!</b>" if is_transfer_tx else "✅ <b>Transazione Registrata con Successo!</b>"
    receipt_line = "" if is_transfer_tx else f"🧾 <b>Ricevuta:</b> <code>#{registered_tx.receipt_number}</code>\n"

    esc_desc = html.escape(registered_tx.description)
    esc_user = html.escape(registered_tx.telegram_username)

    summary = (
        f"{title}\n\n"
        f"🆔 <b>ID Transazione:</b> <code>#{registered_tx.id}</code>\n"
        f"📅 <b>Data:</b> <code>{html.escape(registered_tx.date_time)}</code>\n"
        f"{method_emoji} <b>Metodo:</b> <code>{html.escape(registered_tx.method)}</code>\n"
        f"📝 <b>Descrizione:</b> {esc_desc}\n"
        f"{flow_emoji} <b>Flusso:</b> <code>{html.escape(registered_tx.flow)}</code>\n"
        f"💰 <b>Importo:</b> <code>{registered_tx.amount:.2f} €</code>\n"
        f"{box_info}"
        f"{sat_info}"
        f"{receipt_line}"
        f"👤 <b>Registrato da:</b> @{esc_user} (<code>{registered_tx.telegram_user_id}</code>)"
    )

    await send_msg(update, summary)

    # Chiusura forzata dello stato nel conversation handler
    chat_id = update.effective_chat.id if update.effective_chat else 0
    key = (chat_id, user_id)
    if active_conv_handler and key in active_conv_handler._conversations:
        active_conv_handler._update_state(ConversationHandler.END, key)

    context.user_data.clear()
    return ConversationHandler.END

async def start_write(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Avvia la registrazione guidata con il comando /write o /w"""
    if not update.effective_chat or not is_chat_allowed(update.effective_chat.id):
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["tx_data"] = {}
    return await advance_or_finish(update, context)


async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Avvia il trasferimento di contanti tra Cassa Rossa e Cassa Blu (/transfer)"""
    if not update.effective_chat or not is_chat_allowed(update.effective_chat.id):
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["tx_data"] = {
        "kind": "transfer",
        "date_time": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "method": "Trasferimento",
        "flow": "Trasferimento",
        "receipt_number": 0,
    }
    return await advance_or_finish(update, context)


def make_custom_command_starter(config: CustomCommandConfig):
    """Genera l'handler di avvio per una specifica macro/comando personalizzato"""
    async def custom_starter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.effective_chat or not is_chat_allowed(update.effective_chat.id):
            return ConversationHandler.END

        context.user_data.clear()
        data: Dict[str, Any] = {}
        defaults = config.defaults

        # Pre-compila data e ora se impostata a 'now'
        if defaults.date_time:
            if defaults.date_time.lower() == "now":
                data["date_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            else:
                data["date_time"] = format_datetime_for_sheet(defaults.date_time)

        # Pre-compila metodo
        if defaults.method:
            data["method"] = defaults.method

        # Pre-compila flusso
        if defaults.flow:
            data["flow"] = defaults.flow

        # Pre-compila importo
        if defaults.amount is not None:
            data["amount"] = float(defaults.amount)

        # Pre-compila o prepara descrizione
        prefix = defaults.description_prefix or ""
        suffix = defaults.description_suffix or ""
        if defaults.description:
            data["description"] = f"{prefix}{defaults.description}{suffix}"
        if defaults.description_prefix:
            context.user_data["desc_prefix"] = defaults.description_prefix
        if defaults.description_suffix:
            context.user_data["desc_suffix"] = defaults.description_suffix

        # Pre-compila cassa
        if defaults.box_red is not None:
            data["box_red"] = float(defaults.box_red)
        if defaults.box_blue is not None:
            data["box_blue"] = float(defaults.box_blue)
        if defaults.cash_box and defaults.cash_box.lower() in ("red", "rossa"):
            data["cash_box"] = "red"

        context.user_data["tx_data"] = data
        return await advance_or_finish(update, context)

    return custom_starter


async def handle_datetime_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la risposta alla domanda sulla data e ora"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_NOW:
            data["date_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            return await advance_or_finish(update, context)

    text = update.message.text.strip() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)
    if text == "/now":
        data["date_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        return await advance_or_finish(update, context)

    data["date_time"] = format_datetime_for_sheet(text)
    return await advance_or_finish(update, context)


async def handle_method_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la scelta tra Contanti e Satispay"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_METHOD_CASH:
            data["method"] = "Contanti"
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_METHOD_SATISPAY:
            data["method"] = "Satispay"
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_METHOD_SUMUP:
            data["method"] = "SumUp"
            return await advance_or_finish(update, context)

    text = update.message.text.strip().lower() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)
    if "sumup" in text or "carta" in text or "pos" in text:
        data["method"] = "SumUp"
        return await advance_or_finish(update, context)
    if "contant" in text or "cash" in text:
        data["method"] = "Contanti"
        return await advance_or_finish(update, context)
    if "satispay" in text or "digitale" in text:
        data["method"] = "Satispay"
        return await advance_or_finish(update, context)

    await send_msg(update, "⚠️ Scelta non valida. Premi uno dei pulsanti o digita <b>Contanti</b>, <b>Satispay</b> o <b>SumUp</b>.")
    return STATE_METHOD


async def handle_box_before_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la conferma o l'inserimento manuale dei saldi di Cassa Rossa e Cassa Blu"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_BOX_USE_LAST:
            red, blue = context.user_data.get("suggested_balances", (0.0, 0.0))
            data["box_red"], data["box_blue"] = red, blue
            return await advance_or_finish(update, context)

    text = update.message.text.strip() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)

    parsed = parse_two_amounts(text)
    if parsed is None:
        await send_msg(
            update,
            "⚠️ Inserisci i saldi di 🔴 Rossa e 🔵 Blu nel formato <code>Rossa Blu</code> (es: <code>120 35,50</code>).",
        )
        return STATE_BOX_BEFORE
    data["box_red"], data["box_blue"] = parsed
    return await advance_or_finish(update, context)


async def handle_transfer_direction_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la scelta della direzione del trasferimento"""
    data = context.user_data.setdefault("tx_data", {})
    direction = None

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_TRANSFER_BLUE_TO_RED:
            direction = "blue_to_red"
        elif query_data == CALLBACK_TRANSFER_RED_TO_BLUE:
            direction = "red_to_blue"
    else:
        text = update.message.text.strip().lower() if update.message else ""
        if text == "/cancel":
            return await handle_cancel(update, context)
        # Accetta ad esempio "blu rossa" / "blu -> rossa" (origine prima della destinazione)
        blue_pos, red_pos = text.find("blu"), text.find("ross")
        if blue_pos != -1 and red_pos != -1:
            direction = "blue_to_red" if blue_pos < red_pos else "red_to_blue"

    if direction is None:
        await send_msg(
            update,
            "⚠️ Scegli la direzione del trasferimento con i pulsanti.",
            reply_markup=get_transfer_direction_keyboard(),
        )
        return STATE_TRANSFER_DIRECTION

    data["transfer_direction"] = direction
    data["description"] = "Trasferimento Blu → Rossa" if direction == "blue_to_red" else "Trasferimento Rossa → Blu"
    return await advance_or_finish(update, context)


async def handle_description_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce l'inserimento della descrizione"""
    data = context.user_data.setdefault("tx_data", {})
    text = update.message.text.strip() if update.message else ""

    if text == "/cancel":
        return await handle_cancel(update, context)

    prefix = context.user_data.get("desc_prefix", "")
    suffix = context.user_data.get("desc_suffix", "")
    data["description"] = f"{prefix}{text}{suffix}"

    return await advance_or_finish(update, context)

async def handle_flow_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la scelta del flusso di cassa (Entrata o Uscita)"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_FLOW_IN:
            data["flow"] = "Entrata"
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_FLOW_OUT:
            data["flow"] = "Uscita"
            return await advance_or_finish(update, context)

    text = update.message.text.strip().lower() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)
    if "entrat" in text or "in" in text or text == "+":
        data["flow"] = "Entrata"
        return await advance_or_finish(update, context)
    if "uscit" in text or "out" in text or text == "-":
        data["flow"] = "Uscita"
        return await advance_or_finish(update, context)

    await send_msg(update, "⚠️ Scelta non valida. Premi <b>➕ Entrata</b> o <b>➖ Uscita</b>.")
    return STATE_FLOW


async def handle_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce l'inserimento dell'importo"""
    data = context.user_data.setdefault("tx_data", {})
    text = update.message.text.strip() if update.message else ""

    if text == "/cancel":
        return await handle_cancel(update, context)

    try:
        val = float(text.replace("€", "").replace(",", ".").strip())
        if val <= 0:
            await send_msg(update, "⚠️ L'importo deve essere maggiore di zero.")
            return STATE_AMOUNT
        if data.get("kind") == "transfer":
            from_red = data.get("transfer_direction") == "red_to_blue"
            source_balance = data.get("box_red" if from_red else "box_blue", 0.0)
            if val > source_balance + 1e-9:
                source_name = "🔴 Rossa" if from_red else "🔵 Blu"
                await send_msg(
                    update,
                    f"⚠️ Saldo insufficiente nella Cassa {source_name} ({source_balance:.2f} €). Inserisci un importo minore.",
                )
                return STATE_AMOUNT
        data["amount"] = val
        return await advance_or_finish(update, context)
    except ValueError:
        await send_msg(update, "⚠️ Inserisci una cifra numerica valida per l'importo (es: <code>12.50</code>).")
        return STATE_AMOUNT


async def handle_box_after_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la conferma o inserimento manuale del saldo aggiornato della cassa usata (Blu di default, Rossa se scelta)"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data in (CALLBACK_CASH_USE_RED, CALLBACK_CASH_USE_BLUE):
            data["cash_box"] = "red" if query_data == CALLBACK_CASH_USE_RED else "blue"
            # Ricalcola il passo per la cassa scelta
            data.pop("box_red_updated", None)
            data.pop("box_blue_updated", None)
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_BOX_CONFIRM_CALCULATED:
            data[f"box_{data.get('cash_box', 'blue')}_updated"] = context.user_data.get("suggested_box_after", 0.0)
            return await advance_or_finish(update, context)

    text = update.message.text.strip() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)

    box = data.get("cash_box", "blue")
    try:
        val = float(text.replace("€", "").replace(",", ".").strip())
        data[f"box_{box}_updated"] = val
        return await advance_or_finish(update, context)
    except ValueError:
        box_name = "Rossa" if box == "red" else "Blu"
        await send_msg(update, f"⚠️ Inserisci un importo valido per la Cassa {box_name} aggiornata.")
        return STATE_BOX_AFTER


async def handle_receipt_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce l'inserimento o conferma del numero di ricevuta"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_RECEIPT_USE_SUGGESTED:
            data["receipt_number"] = context.user_data.get("suggested_receipt", 0)
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_RECEIPT_NONE:
            data["receipt_number"] = 0
            return await advance_or_finish(update, context)

    text = update.message.text.strip() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)

    if text.isdigit():
        data["receipt_number"] = int(text)
        return await advance_or_finish(update, context)
    else:
        await send_msg(update, "⚠️ Inserisci un numero intero per la ricevuta (oppure <code>0</code> se assente).")
        return STATE_RECEIPT


async def handle_qr_ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la scelta se generare o saltare il QR Code Satispay"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_QR_SKIP:
            data["qr_choice_done"] = True
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_QR_GENERATE:
            return await start_qr_payment_flow(update, context)

    text = update.message.text.strip().lower() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)
    if "salta" in text or "skip" in text or "no" in text:
        data["qr_choice_done"] = True
        return await advance_or_finish(update, context)
    if "qr" in text or "genera" in text or "si" in text or "sì" in text:
        return await start_qr_payment_flow(update, context)

    await send_msg(
        update,
        "⚠️ Scegli un'opzione: premi <b>📱 Genera QR Code</b> oppure <b>⏩ Salta e registra subito</b>.",
        reply_markup=get_qr_ask_keyboard()
    )
    return STATE_QR_ASK


async def start_qr_payment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Crea la richiesta di pagamento su Satispay, genera il QR Code e avvia l'attesa del pagamento"""
    data = context.user_data.setdefault("tx_data", {})
    data["qr_choice_done"] = True

    amount = data.get("amount", 0.0)
    amount_unit = int(round(amount * 100))

    # Nota per la transazione Satispay: Descrizione + Numero Ricevuta (o '-' se assente)
    base_desc = (data.get("description") or "Transazione").strip()
    receipt_num = data.get("receipt_number", 0)
    receipt_str = str(receipt_num) if receipt_num and receipt_num > 0 else "-"
    desc = f"{base_desc} - {receipt_str}"

    await send_msg(update, "⏳ <i>Contatto Satispay per generare il codice QR dinamico...</i>")

    payment_res = await satispay_service.create_payment(amount_unit, description=desc)
    if not payment_res or not payment_res.get("id"):
        await send_msg(
            update,
            "⚠️ <b>Impossibile generare il QR Code Satispay.</b> Registro la transazione normalmente sul foglio...",
        )
        return await advance_or_finish(update, context)

    payment_id = payment_res["id"]
    redirect_url = payment_res.get("redirect_url") or f"https://online.satispay.com/pay/{payment_id}"
    context.user_data["pending_payment_id"] = payment_id

    # Genera l'immagine QR
    qr_buffer = satispay_service.generate_qr_code_image(redirect_url)

    caption = (
        f"{get_user_mention(update)}\n"
        f"📲 <b>Inquadra con l'app Satispay per pagare {amount:.2f} €!</b>\n\n"
        f"🆔 <b>ID Satispay:</b> <code>{html.escape(payment_id)}</code>\n"
        "⏳ <i>In attesa che il cliente autorizzi il pagamento sull'app...</i>\n\n"
        "💡 La transazione verrà registrata e collegata in automatico a pagamento avvenuto.\n"
        "Se ci sono problemi, puoi premere <b>⏩ Salta pagamento e registra</b>."
    )

    await update.effective_chat.send_photo(
        photo=qr_buffer,
        caption=caption,
        parse_mode="HTML",
        reply_markup=get_qr_waiting_keyboard()
    )

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    asyncio.create_task(
        wait_for_qr_payment(
            chat_id=chat_id,
            user_id=user_id,
            payment_id=payment_id,
            amount=amount,
            context=context,
            update=update
        )
    )
    return STATE_QR_WAITING


async def wait_for_qr_payment(
    chat_id: int,
    user_id: int,
    payment_id: str,
    amount: float,
    context: ContextTypes.DEFAULT_TYPE,
    update: Update
):
    """
    Loop di attesa asincrono: controlla ogni 3 secondi se il pagamento QR Code è stato autorizzato
    """
    for _ in range(60):  # Massimo 3 minuti (60 * 3s)
        await asyncio.sleep(3)

        # Se l'utente ha interrotto manualmente
        if context.user_data.get("qr_cancelled") or context.user_data.get("qr_skipped"):
            logger.info(f"Attesa QR Satispay {payment_id} interrotta dall'utente.")
            return

        try:
            payment = await satispay_service.get_payment(payment_id)
            if payment and payment.status == "ACCEPTED":
                logger.info(f"Pagamento QR Satispay {payment_id} ACCETTATO con successo!")
                context.user_data.setdefault("tx_data", {})["satispay_id"] = payment_id
                # Salva in SQLite per evitare duplicati col polling generale
                db_service.save_payment(payment)

                sender_str = f" da <b>{html.escape(payment.sender_name)}</b>" if payment.sender_name else ""
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ <b>Pagamento Satispay di {amount:.2f} € ricevuto con successo{sender_str}!</b>",
                    parse_mode="HTML"
                )
                try:
                    await finalize_and_save_transaction(update, context)
                except Exception as ex:
                    logger.error(f"Errore durante finalizzazione transazione QR: {ex}")
                return
            elif payment and payment.status in ("CANCELED", "EXPIRED"):
                logger.info(f"Pagamento QR Satispay {payment_id} terminato con esito: {payment.status}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ <b>Il pagamento Satispay è risultato: {html.escape(payment.status)}.</b> Procedo con la registrazione della transazione sul foglio...",
                    parse_mode="HTML"
                )
                try:
                    await finalize_and_save_transaction(update, context)
                except Exception as ex:
                    logger.error(f"Errore durante finalizzazione transazione QR: {ex}")
                return
        except Exception as e:
            logger.error(f"Errore durante verifica stato QR {payment_id}: {e}")

    # Timeout di 3 minuti
    if not context.user_data.get("qr_cancelled") and not context.user_data.get("qr_skipped"):
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ <b>Tempo scaduto per il pagamento del QR Code.</b> Procedo con la registrazione sul foglio senza collegamento automatico...",
            parse_mode="HTML"
        )
        await finalize_and_save_transaction(update, context)


async def handle_qr_waiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce l'input durante l'attesa del pagamento QR Code (salta o annulla)"""
    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            context.user_data["qr_cancelled"] = True
            return await handle_cancel(update, context)
        if query_data == CALLBACK_QR_SKIP:
            context.user_data["qr_skipped"] = True
            await send_msg(update, "⏩ <b>Pagamento QR saltato.</b> Registro subito la transazione sul foglio...")
            return await finalize_and_save_transaction(update, context)

    text = update.message.text.strip().lower() if update.message else ""
    if text == "/cancel":
        context.user_data["qr_cancelled"] = True
        return await handle_cancel(update, context)
    if "salta" in text or "skip" in text:
        context.user_data["qr_skipped"] = True
        await send_msg(update, "⏩ <b>Pagamento QR saltato.</b> Registro subito la transazione sul foglio...")
        return await finalize_and_save_transaction(update, context)

    await send_msg(
        update,
        "⏳ <b>In attesa del pagamento...</b>\n"
        "Premi <b>⏩ Salta pagamento e registra</b> per procedere subito o <b>❌ Annulla operazione</b> per terminare.",
        reply_markup=get_qr_waiting_keyboard()
    )
    return STATE_QR_WAITING


async def handle_sumup_ask_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la scelta di incasso SumUp: terminale, link/QR (se abilitato) oppure salta"""
    data = context.user_data.setdefault("tx_data", {})
    qr_enabled = settings.SUMUP_QR_ENABLED

    if update.callback_query:
        await ack_callback_query(update)
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_QR_SKIP:
            data["sumup_choice_done"] = True
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_SUMUP_TERMINAL:
            return await start_sumup_terminal_wait(update, context)
        if query_data == CALLBACK_SUMUP_LINK and qr_enabled:
            return await start_sumup_link_flow(update, context)
    else:
        text = update.message.text.strip().lower() if update.message else ""
        if text == "/cancel":
            return await handle_cancel(update, context)
        if "salta" in text or "skip" in text:
            data["sumup_choice_done"] = True
            return await advance_or_finish(update, context)
        if "termin" in text or "pos" in text:
            return await start_sumup_terminal_wait(update, context)
        if qr_enabled and ("qr" in text or "link" in text):
            return await start_sumup_link_flow(update, context)

    await send_msg(
        update,
        "⚠️ Scegli un'opzione usando i pulsanti sottostanti.",
        reply_markup=get_sumup_ask_keyboard(qr_enabled),
    )
    return STATE_SUMUP_ASK


async def start_sumup_terminal_wait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Attende un pagamento con lo stesso importo sul terminale SumUp e lo collega alla transazione"""
    data = context.user_data.setdefault("tx_data", {})
    data["sumup_choice_done"] = True

    if not sumup_service.is_configured:
        await send_msg(update, "⚠️ <b>SumUp non è configurato (SUMUP_API_KEY mancante).</b> Registro la transazione normalmente...")
        return await advance_or_finish(update, context)

    amount = data.get("amount", 0.0)
    since = datetime.now(timezone.utc)
    claimed = context.application.bot_data.setdefault("sumup_claimed_codes", set())

    async def check():
        match = await sumup_service.find_new_payment(since, exclude_codes=claimed)
        if match:
            claimed.add(match.transaction_code)
            return ("PAID", match.transaction_code)
        return None

    await send_msg(
        update,
        f"💳 <b>Esegui il pagamento di {amount:.2f} € sul terminale SumUp 3G+.</b>\n"
        "⏳ <i>In attesa del pagamento...</i>\n\n"
        "💡 Il bot collegherà la prima transazione SumUp completata, senza verificarne l'importo "
        "(sul foglio resta quello inserito).\n"
        "Se il pagamento non viene rilevato, premi <b>⏩ Salta pagamento e registra</b>: "
        "la transazione verrà registrata senza ID SumUp.",
        reply_markup=get_qr_waiting_keyboard(),
    )
    _spawn_sumup_wait(update, context, check, amount)
    return STATE_SUMUP_WAITING


async def start_sumup_link_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Crea un checkout SumUp con link di pagamento, mostra il QR e attende il pagamento"""
    data = context.user_data.setdefault("tx_data", {})
    data["sumup_choice_done"] = True

    amount = data.get("amount", 0.0)
    base_desc = (data.get("description") or "Transazione").strip()
    receipt_num = data.get("receipt_number", 0)
    receipt_str = str(receipt_num) if receipt_num and receipt_num > 0 else "-"

    await send_msg(update, "⏳ <i>Contatto SumUp per generare il link di pagamento...</i>")
    checkout = await sumup_service.create_hosted_checkout(amount, description=f"{base_desc} - {receipt_str}")
    if not checkout:
        await send_msg(
            update,
            "⚠️ <b>Impossibile generare il link di pagamento SumUp.</b> Registro la transazione normalmente sul foglio...",
        )
        return await advance_or_finish(update, context)

    checkout_id = checkout["id"]
    url = checkout["hosted_checkout_url"]
    qr_buffer = generate_qr_code_image(url)

    caption = (
        f"{get_user_mention(update)}\n"
        f"📲 <b>Inquadra il QR per pagare {amount:.2f} € con carta!</b>\n\n"
        f"🔗 {html.escape(url)}\n"
        "⏳ <i>In attesa del pagamento...</i>\n\n"
        "💡 La transazione verrà registrata e collegata in automatico a pagamento avvenuto.\n"
        "Se il pagamento non viene rilevato, premi <b>⏩ Salta pagamento e registra</b>: "
        "la transazione verrà registrata senza ID SumUp."
    )
    await update.effective_chat.send_photo(
        photo=qr_buffer, caption=caption, parse_mode="HTML", reply_markup=get_qr_waiting_keyboard()
    )

    async def check():
        checkout_data = await sumup_service.get_checkout(checkout_id)
        return sumup_service.checkout_result(checkout_data) if checkout_data else None

    _spawn_sumup_wait(update, context, check, amount)
    return STATE_SUMUP_WAITING


def _spawn_sumup_wait(update: Update, context: ContextTypes.DEFAULT_TYPE, check_fn, amount: float) -> None:
    asyncio.create_task(
        wait_for_sumup_payment(
            chat_id=update.effective_chat.id,
            check_fn=check_fn,
            amount=amount,
            context=context,
            update=update,
        )
    )


async def wait_for_sumup_payment(chat_id: int, check_fn, amount: float, context: ContextTypes.DEFAULT_TYPE, update: Update):
    """
    Loop di attesa asincrono: controlla ogni 4 secondi l'esito del pagamento SumUp tramite check_fn,
    che restituisce ("PAID", codice), ("FAILED", stato) oppure None se ancora in attesa.
    """
    attempts = max(1, settings.SUMUP_WAIT_TIMEOUT // 4)
    for _ in range(attempts):
        await asyncio.sleep(4)

        if context.user_data.get("qr_cancelled") or context.user_data.get("qr_skipped"):
            logger.info("Attesa pagamento SumUp interrotta dall'utente.")
            return

        try:
            result = await check_fn()
        except Exception as e:
            logger.error(f"Errore durante verifica pagamento SumUp: {e}")
            continue

        if not result:
            continue

        outcome, value = result
        if outcome == "PAID":
            logger.info(f"Pagamento SumUp ricevuto: {value}")
            if value:
                context.user_data.setdefault("tx_data", {})["sumup_id"] = value
                # Salva in SQLite per evitare notifiche doppie dal polling generale
                tx = await sumup_service.get_transaction(value)
                if tx:
                    db_service.save_sumup_transaction(tx)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ <b>Pagamento SumUp ricevuto e collegato</b> (codice <code>{html.escape(str(value))}</code>).",
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ <b>Il pagamento SumUp è risultato: {html.escape(str(value))}.</b> Procedo con la registrazione della transazione sul foglio...",
                parse_mode="HTML",
            )
        try:
            await finalize_and_save_transaction(update, context)
        except Exception as ex:
            logger.error(f"Errore durante finalizzazione transazione SumUp: {ex}")
        return

    if not context.user_data.get("qr_cancelled") and not context.user_data.get("qr_skipped"):
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ <b>Tempo scaduto per il pagamento SumUp.</b> Procedo con la registrazione sul foglio senza collegamento automatico...",
            parse_mode="HTML",
        )
        await finalize_and_save_transaction(update, context)


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Annulla l'operazione in corso e azzera lo stato"""
    await ack_callback_query(update)
    context.user_data.clear()
    await send_msg(update, "❌ Operazione annullata. Nessun dato è stato registrato.")
    return ConversationHandler.END

def build_conversation_handler(custom_commands: Optional[Dict[str, CustomCommandConfig]] = None) -> ConversationHandler:
    """Costruisce il ConversationHandler registrando i comandi base e tutte le macro custom"""
    entry_points = [
        CommandHandler(["write", "w"], start_write),
        CommandHandler(["transfer", "trasferimento", "tr"], start_transfer),
    ]

    if custom_commands:
        for cmd_name, cmd_cfg in custom_commands.items():
            entry_points.append(
                CommandHandler(cmd_name, make_custom_command_starter(cmd_cfg))
            )

    states = {
        STATE_DATETIME: [
            CallbackQueryHandler(handle_datetime_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_datetime_input),
            CommandHandler("now", handle_datetime_input),
        ],
        STATE_METHOD: [
            CallbackQueryHandler(handle_method_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_method_input),
        ],
        STATE_TRANSFER_DIRECTION: [
            CallbackQueryHandler(handle_transfer_direction_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_transfer_direction_input),
        ],
        STATE_BOX_BEFORE: [
            CallbackQueryHandler(handle_box_before_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_box_before_input),
        ],
        STATE_DESCRIPTION: [
            CallbackQueryHandler(handle_cancel, pattern=f"^{CALLBACK_CANCEL}$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description_input),
        ],
        STATE_FLOW: [
            CallbackQueryHandler(handle_flow_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_flow_input),
        ],
        STATE_AMOUNT: [
            CallbackQueryHandler(handle_cancel, pattern=f"^{CALLBACK_CANCEL}$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount_input),
        ],
        STATE_BOX_AFTER: [
            CallbackQueryHandler(handle_box_after_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_box_after_input),
        ],
        STATE_RECEIPT: [
            CallbackQueryHandler(handle_receipt_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receipt_input),
        ],
        STATE_QR_ASK: [
            CallbackQueryHandler(handle_qr_ask_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_qr_ask_input),
        ],
        STATE_QR_WAITING: [
            CallbackQueryHandler(handle_qr_waiting_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_qr_waiting_input),
        ],
        STATE_SUMUP_ASK: [
            CallbackQueryHandler(handle_sumup_ask_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sumup_ask_input),
        ],
        STATE_SUMUP_WAITING: [
            CallbackQueryHandler(handle_qr_waiting_input),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_qr_waiting_input),
        ],
    }

    fallbacks = [
        CommandHandler("cancel", handle_cancel),
        CallbackQueryHandler(handle_cancel, pattern=f"^{CALLBACK_CANCEL}$"),
    ]

    global active_conv_handler
    active_conv_handler = ConversationHandler(
        entry_points=entry_points,
        states=states,
        fallbacks=fallbacks,
        per_message=False,
    )
    return active_conv_handler

