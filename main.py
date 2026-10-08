import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from config import settings
from core.models import SatispayPayment
from services.sheets_service import get_sheets_service
from services.satispay_service import satispay_service
from services.db_service import db_service
from bot.custom_commands import load_custom_commands, build_bot_commands_list
from bot.conversation import build_conversation_handler
from bot.handlers import (
    start_handler,
    help_handler,
    link_handler,
    unlink_handler,
    sat_list_handler,
    sat_list_range_handler,
    sat_get_handler,
    error_handler,
    format_satispay_echo,
)

# Creazione cartella logs e generazione file di log con data e ora di avvio
logs_dir = Path("logs")
logs_dir.mkdir(parents=True, exist_ok=True)
log_filename = logs_dir / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Configurazione del logger per scrivere sia su file che su console
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(str(log_filename), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("GdG-Ledger-Bot")
logger.info(f"Logging inizializzato. File di log della sessione: {log_filename}")

# Disabilita i log prolissi di httpx, httpcore e apscheduler
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def send_satispay_echo(bot, payment: SatispayPayment):
    """
    Invia il messaggio di echo della transazione Satispay nella chat Telegram autorizzata
    """
    chat_id = settings.TELEGRAM_ALLOWED_CHAT_ID
    if not chat_id:
        logger.warning("TELEGRAM_ALLOWED_CHAT_ID non configurato.")
        return

    echo_text = format_satispay_echo(payment)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=echo_text,
            parse_mode="HTML"
        )
        logger.info(f"Notifica Satispay {payment.id} ({payment.amount_euro:.2f}€) inviata alla chat {chat_id}")
    except Exception as e:
        logger.error(f"Errore durante l'invio della notifica Satispay su Telegram: {e}")


async def poll_satispay_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Job periodico che interroga le API Satispay e notifica eventuali nuovi pagamenti
    """
    try:
        new_payments = await satispay_service.poll_new_payments()
        for p in new_payments:
            await send_satispay_echo(context.bot, p)
    except Exception as e:
        logger.error(f"Errore durante il job di polling Satispay: {e}")


async def post_init(application):
    """
    Inizializzazione post-avvio del bot: verifica foglio di calcolo e registra setMyCommands
    """
    sheets = get_sheets_service()
    try:
        sheets.ensure_headers()
        logger.info("Foglio Google Sheets verificato con successo.")
    except Exception as e:
        logger.warning(f"Impossibile verificare intestazioni Google Sheets: {e}")

    custom_cmds = application.bot_data.get("custom_commands", {})
    try:
        bot_commands = build_bot_commands_list(custom_cmds)
        await application.bot.set_my_commands(bot_commands)
        logger.info("Comandi registrati con successo con Telegram setMyCommands.")
    except Exception as e:
        logger.warning(f"Impossibile registrare setMyCommands su Telegram: {e}")


def create_telegram_app():
    """
    Inizializza l'applicazione Telegram con tutti gli handler registrati e la JobQueue
    """
    custom_cmds = load_custom_commands()
    logger.info(f"Comandi personalizzati caricati: {list(custom_cmds.keys())}")

    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .get_updates_read_timeout(30.0)
        .read_timeout(30.0)
        .connect_timeout(30.0)
        .post_init(post_init)
        .build()
    )
    app.bot_data["custom_commands"] = custom_cmds

    # Registra il ConversationHandler principale (/write, /w e custom commands)
    conv_handler = build_conversation_handler(custom_cmds)
    app.add_handler(conv_handler)

    # Registra handler comandi base
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("link", link_handler))
    app.add_handler(CommandHandler("unlink", unlink_handler))

    # Registra handler comandi storico Satispay
    app.add_handler(CommandHandler(["sat_list", "sl"], sat_list_handler))
    app.add_handler(CommandHandler(["sat_list_range", "slr"], sat_list_range_handler))
    app.add_handler(CommandHandler(["sat_get", "sg"], sat_get_handler))

    # Handler errori
    app.add_error_handler(error_handler)

    # Configurazione JobQueue per il Polling periodico di Satispay
    poll_interval = max(5, settings.SATISPAY_POLL_INTERVAL)
    app.job_queue.run_repeating(poll_satispay_job, interval=poll_interval, first=2)
    logger.info(f"JobQueue Satispay Polling configurato ogni {poll_interval} secondi.")

    return app


def main():
    """
    Punto di ingresso principale: avvia il bot Telegram in polling nativo
    """
    logger.info("Avvio GdG-Ledger-Bot (Modalità Polling autonomo)...")
    app = create_telegram_app()
    app.run_polling()


if __name__ == "__main__":
    main()

