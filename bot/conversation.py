import asyncio
import html
import logging
from datetime import datetime
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
from core.models import Transaction, CustomCommandConfig, format_datetime_for_sheet
from core.security import is_chat_allowed
from services.sheets_service import get_sheets_service
from services.satispay_service import satispay_service
from services.db_service import db_service
from bot.keyboards import (
    get_now_keyboard,
    get_method_keyboard,
    get_box_money_keyboard,
    get_flow_keyboard,
    get_box_updated_keyboard,
    get_receipt_keyboard,
    get_cancel_keyboard,
    get_qr_ask_keyboard,
    get_qr_waiting_keyboard,
    CALLBACK_NOW,
    CALLBACK_METHOD_CASH,
    CALLBACK_METHOD_SATISPAY,
    CALLBACK_FLOW_IN,
    CALLBACK_FLOW_OUT,
    CALLBACK_BOX_USE_LAST,
    CALLBACK_BOX_CONFIRM_CALCULATED,
    CALLBACK_RECEIPT_USE_SUGGESTED,
    CALLBACK_RECEIPT_NONE,
    CALLBACK_CANCEL,
    CALLBACK_QR_GENERATE,
    CALLBACK_QR_SKIP,
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
) = range(10)


def get_user_mention(update: Update) -> str:
    """Restituisce il tag esplicito dell'utente (username o menzione Telegram)"""
    user = update.effective_user
    if not user:
        return ""
    if user.username:
        return f"👤 @{html.escape(user.username)}"
    safe_name = html.escape(user.first_name or "Utente")
    return f'👤 <a href="tg://user?id={user.id}">{safe_name}</a>'


async def send_msg(update: Update, text: str, reply_markup=None, tag_user: bool = True):
    """Helper per inviare o modificare un messaggio in base al tipo di update con tag esplicito utente"""
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception as e:
            logger.debug(f"Impossibile rispondere alla callback query (forse scaduta): {e}")

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

    # 3. Cassa Iniziale / Conferma Saldo Cassa Attuale
    if "box_money" not in data:
        last_box = sheets.get_last_box_money()
        context.user_data["suggested_last_box"] = last_box

        if data.get("method") == "Contanti":
            prompt_title = "🪙 <b>Cassa iniziale (prima della transazione):</b>\n"
            prompt_desc = (
                f"Ultimo saldo contanti rilevato nel registro: <b>{last_box:.2f} €</b>\n\n"
                "Premi il pulsante per confermarlo oppure scrivi l'importo manualmente."
            )
        else:
            prompt_title = "🪙 <b>Conferma saldo cassa attuale:</b>\n"
            prompt_desc = (
                f"Ultimo saldo contanti rilevato nel registro: <b>{last_box:.2f} €</b>\n\n"
                "Verifica e conferma il denaro presente in cassa al momento della transazione, "
                "anche se il pagamento è effettuato tramite Satispay.\n"
                "Premi il pulsante per confermarlo oppure scrivi l'importo manualmente."
            )

        await send_msg(
            update,
            f"{prompt_title}{prompt_desc}",
            reply_markup=get_box_money_keyboard(last_box),
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

    # 7. Cassa Aggiornata (solo se Contanti, altrimenti allineata a cassa attuale)
    if "box_money_updated" not in data:
        if data.get("method") == "Contanti":
            box_init = data.get("box_money", 0.0)
            amount = data.get("amount", 0.0)
            flow = data.get("flow", "Entrata")
            calc_box = box_init + amount if flow == "Entrata" else box_init - amount
            context.user_data["suggested_box_after"] = calc_box

            sign = "+" if flow == "Entrata" else "-"
            await send_msg(
                update,
                f"🪙 <b>Cassa aggiornata (dopo la transazione):</b>\n"
                f"Saldo calcolato: <b>{calc_box:.2f} €</b> ({box_init:.2f} € {sign} {amount:.2f} €)\n\n"
                "Premi il pulsante per confermare il saldo calcolato oppure scrivi il valore reale se differisce.",
                reply_markup=get_box_updated_keyboard(calc_box),
            )
            return STATE_BOX_AFTER
        else:
            # Per Satispay il denaro in cassa non varia; la cassa aggiornata (Colonna H) coincide con la cassa iniziale (Colonna D)
            data["box_money_updated"] = data.get("box_money", 0.0)

    # 8. Numero Ricevuta
    if "receipt_number" not in data:
        last_receipt = sheets.get_last_receipt_number()
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

    # Per Satispay, allinea cassa iniziale e cassa aggiornata al saldo confermato
    if data.get("method") == "Satispay":
        if data.get("box_money_updated") is None and data.get("box_money") is not None:
            data["box_money_updated"] = data.get("box_money")
        elif data.get("box_money") is None and data.get("box_money_updated") is not None:
            data["box_money"] = data.get("box_money_updated")

    tx = Transaction(
        date_time=data["date_time"],
        method=data["method"],
        box_money=data.get("box_money"),
        description=data["description"],
        flow=data["flow"],
        amount=data["amount"],
        box_money_updated=data.get("box_money_updated"),
        receipt_number=data.get("receipt_number", 0),
        telegram_username=username,
        telegram_user_id=user_id,
        satispay_id=data.get("satispay_id"),
    )

    try:
        registered_tx = sheets.add_transaction(tx)
    except Exception as e:
        logger.error(f"Errore durante salvataggio su foglio: {e}")
        await send_msg(
            update,
            f"❌ <b>Errore durante il salvataggio sul foglio di calcolo:</b>\n<code>{html.escape(str(e))}</code>"
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Messaggio riepilogo inviato in chat
    box_info = ""
    if registered_tx.method == "Contanti":
        b_init = f"{registered_tx.box_money:.2f} €" if registered_tx.box_money is not None else "-"
        b_upd = f"{registered_tx.box_money_updated:.2f} €" if registered_tx.box_money_updated is not None else "-"
        box_info = f"🪙 <b>Cassa iniziale:</b> <code>{b_init}</code>\n🪙 <b>Cassa aggiornata:</b> <code>{b_upd}</code>\n"
    elif registered_tx.box_money is not None:
        b_val = f"{registered_tx.box_money:.2f} €"
        box_info = f"🪙 <b>Cassa attuale confermata:</b> <code>{b_val}</code>\n"

    sat_info = ""
    if registered_tx.satispay_id:
        sat_info = f"📱 <b>ID Satispay Collegato:</b> <code>{html.escape(registered_tx.satispay_id)}</code>\n"

    flow_emoji = "➕" if registered_tx.flow == "Entrata" else "➖"
    method_emoji = "💵" if registered_tx.method == "Contanti" else "📱"

    esc_desc = html.escape(registered_tx.description)
    esc_user = html.escape(registered_tx.telegram_username)

    summary = (
        "✅ <b>Transazione Registrata con Successo!</b>\n\n"
        f"🆔 <b>ID Transazione:</b> <code>#{registered_tx.id}</code>\n"
        f"📅 <b>Data:</b> <code>{html.escape(registered_tx.date_time)}</code>\n"
        f"{method_emoji} <b>Metodo:</b> <code>{html.escape(registered_tx.method)}</code>\n"
        f"📝 <b>Descrizione:</b> {esc_desc}\n"
        f"{flow_emoji} <b>Flusso:</b> <code>{html.escape(registered_tx.flow)}</code>\n"
        f"💰 <b>Importo:</b> <code>{registered_tx.amount:.2f} €</code>\n"
        f"{box_info}"
        f"{sat_info}"
        f"🧾 <b>Ricevuta:</b> <code>#{registered_tx.receipt_number}</code>\n"
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
        if defaults.box_money is not None:
            data["box_money"] = float(defaults.box_money)
            if defaults.method == "Satispay":
                data["box_money_updated"] = float(defaults.box_money)

        context.user_data["tx_data"] = data
        return await advance_or_finish(update, context)

    return custom_starter


async def handle_datetime_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la risposta alla domanda sulla data e ora"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
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
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_METHOD_CASH:
            data["method"] = "Contanti"
            return await advance_or_finish(update, context)
        if query_data == CALLBACK_METHOD_SATISPAY:
            data["method"] = "Satispay"
            return await advance_or_finish(update, context)

    text = update.message.text.strip().lower() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)
    if "contant" in text or "cash" in text:
        data["method"] = "Contanti"
        return await advance_or_finish(update, context)
    if "satispay" in text or "digitale" in text:
        data["method"] = "Satispay"
        return await advance_or_finish(update, context)

    await send_msg(update, "⚠️ Scelta non valida. Premi uno dei pulsanti o digita <b>Contanti</b> o <b>Satispay</b>.")
    return STATE_METHOD


async def handle_box_before_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce l'inserimento o conferma della cassa iniziale"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_BOX_USE_LAST:
            data["box_money"] = context.user_data.get("suggested_last_box", 0.0)
            return await advance_or_finish(update, context)

    text = update.message.text.strip() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)

    try:
        val = float(text.replace("€", "").replace(",", ".").strip())
        data["box_money"] = val
        return await advance_or_finish(update, context)
    except ValueError:
        await send_msg(update, "⚠️ Inserisci una cifra numerica valida per la cassa (es: <code>50.00</code>).")
        return STATE_BOX_BEFORE


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
        data["amount"] = val
        return await advance_or_finish(update, context)
    except ValueError:
        await send_msg(update, "⚠️ Inserisci una cifra numerica valida per l'importo (es: <code>12.50</code>).")
        return STATE_AMOUNT


async def handle_box_after_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce la conferma o inserimento manuale della cassa aggiornata"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
        query_data = update.callback_query.data
        if query_data == CALLBACK_CANCEL:
            return await handle_cancel(update, context)
        if query_data == CALLBACK_BOX_CONFIRM_CALCULATED:
            data["box_money_updated"] = context.user_data.get("suggested_box_after", 0.0)
            return await advance_or_finish(update, context)

    text = update.message.text.strip() if update.message else ""
    if text == "/cancel":
        return await handle_cancel(update, context)

    try:
        val = float(text.replace("€", "").replace(",", ".").strip())
        data["box_money_updated"] = val
        return await advance_or_finish(update, context)
    except ValueError:
        await send_msg(update, "⚠️ Inserisci un importo valido per la cassa aggiornata.")
        return STATE_BOX_AFTER


async def handle_receipt_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gestisce l'inserimento o conferma del numero di ricevuta"""
    data = context.user_data.setdefault("tx_data", {})

    if update.callback_query:
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
        "⏳ <b>In attesa del pagamento tramite app Satispay...</b>\n"
        "Premi <b>⏩ Salta pagamento e registra</b> per procedere subito o <b>❌ Annulla operazione</b> per terminare.",
        reply_markup=get_qr_waiting_keyboard()
    )
    return STATE_QR_WAITING


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Annulla l'operazione in corso e azzera lo stato"""
    context.user_data.clear()
    await send_msg(update, "❌ Operazione annullata. Nessun dato è stato registrato.")
    return ConversationHandler.END

def build_conversation_handler(custom_commands: Optional[Dict[str, CustomCommandConfig]] = None) -> ConversationHandler:
    """Costruisce il ConversationHandler registrando i comandi base e tutte le macro custom"""
    entry_points = [
        CommandHandler(["write", "w"], start_write),
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

