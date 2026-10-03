import tempfile
import pytest
from config import settings
from core.security import is_chat_allowed
from core.models import SatispayPayment
from bot.handlers import extract_satispay_id_from_text, format_satispay_echo
from services.db_service import DatabaseService


def test_is_chat_allowed():
    settings.TELEGRAM_ALLOWED_CHAT_ID = 12345
    assert is_chat_allowed(12345) is True
    assert is_chat_allowed(99999) is False
    assert is_chat_allowed(-100123) is False


def test_extract_satispay_id_from_text():
    sample_text = (
        "🔔 *Notifica Pagamento Satispay*\n\n"
        "📥 *Ricevuto pagamento da:* Mario Rossi\n"
        "💰 *Importo:* `+15.00 €`\n"
        "🆔 *ID Satispay:* `e8f52f8d-69f2-4e89-9a74-954f9a0c71bd`\n"
    )
    extracted = extract_satispay_id_from_text(sample_text)
    assert extracted == "e8f52f8d-69f2-4e89-9a74-954f9a0c71bd"

    sample_simple = "ID Satispay: sat_custom_123"
    extracted_simple = extract_satispay_id_from_text(sample_simple)
    assert extracted_simple == "sat_custom_123"

    sample_no_id = "Questo è un messaggio normale senza identificativo"
    assert extract_satispay_id_from_text(sample_no_id) is None


def test_format_satispay_echo():
    payment = SatispayPayment(
        id="sat-test-echo",
        amount_unit=2000,
        currency="EUR",
        status="ACCEPTED",
        flow="MATCH_CODE",
        sender_name="Paolo Neri",
        comment="Quota 2026",
        insert_date="2026-09-30 11:00:00"
    )
    msg = format_satispay_echo(payment)
    assert "sat-test-echo" in msg
    assert "+20.00 €" in msg
    assert "Paolo Neri" in msg
    assert "/link <id_transazione>" in msg


def test_db_service_date_and_range_filtering():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = DatabaseService(tmp.name)
        assert db.is_empty() is True

        p1 = SatispayPayment(
            id="sat_day1",
            amount_unit=1000,
            status="ACCEPTED",
            sender_name="User1",
            insert_date="2026-09-15 10:00:00"
        )
        p2 = SatispayPayment(
            id="sat_day2",
            amount_unit=2500,
            status="ACCEPTED",
            sender_name="User2",
            insert_date="2026-09-20 15:30:00"
        )
        p3 = SatispayPayment(
            id="sat_day3",
            amount_unit=500,
            status="ACCEPTED",
            sender_name="User3",
            insert_date="2026-09-20 18:00:00"
        )

        db.save_payment(p1)
        db.save_payment(p2)
        db.save_payment(p3)

        assert db.is_empty() is False

        # Query per data singola
        day_results = db.get_payments_by_date("20/09/2026")
        assert len(day_results) == 2
        assert {p.id for p in day_results} == {"sat_day2", "sat_day3"}

        # Query per range di date
        range_results = db.get_payments_by_range("10/09/2026", "25/09/2026")
        assert len(range_results) == 3

        range_narrow = db.get_payments_by_range("19/09/2026", "21/09/2026")
        assert len(range_narrow) == 2


def test_get_user_mention():
    from unittest.mock import MagicMock
    from bot.conversation import get_user_mention

    # Utente con username
    update_with_username = MagicMock()
    update_with_username.effective_user.username = "mario_rossi"
    update_with_username.effective_user.first_name = "Mario"
    update_with_username.effective_user.id = 111
    assert get_user_mention(update_with_username) == "👤 @mario_rossi"

    # Utente senza username
    update_no_username = MagicMock()
    update_no_username.effective_user.username = None
    update_no_username.effective_user.first_name = "Luigi"
    update_no_username.effective_user.id = 222
    assert get_user_mention(update_no_username) == "👤 [Luigi](tg://user?id=222)"

    # Nessun utente
    update_none = MagicMock()
    update_none.effective_user = None
    assert get_user_mention(update_none) == ""


def test_qr_keyboards():
    from bot.keyboards import get_qr_ask_keyboard, get_qr_waiting_keyboard, CALLBACK_QR_GENERATE, CALLBACK_QR_SKIP

    ask_kb = get_qr_ask_keyboard()
    callbacks = [btn.callback_data for row in ask_kb.inline_keyboard for btn in row]
    assert CALLBACK_QR_GENERATE in callbacks
    assert CALLBACK_QR_SKIP in callbacks

    wait_kb = get_qr_waiting_keyboard()
    wait_callbacks = [btn.callback_data for row in wait_kb.inline_keyboard for btn in row]
    assert CALLBACK_QR_SKIP in wait_callbacks



@pytest.mark.asyncio
async def test_error_handler_network_error(caplog):
    import logging
    from unittest.mock import MagicMock
    from telegram.error import NetworkError, TimedOut
    import httpx
    from bot.handlers import error_handler

    mock_context = MagicMock()
    mock_context.error = NetworkError("httpx.ReadError: ")

    with caplog.at_level(logging.WARNING):
        await error_handler(None, mock_context)

    assert "Problema di rete temporaneo durante la comunicazione con Telegram" in caplog.text
    # Non deve essere registrato come ERROR
    assert not any(record.levelno == logging.ERROR for record in caplog.records)

    # Test anche con TimedOut
    caplog.clear()
    mock_context.error = TimedOut("Request timed out")
    with caplog.at_level(logging.WARNING):
        await error_handler(None, mock_context)
    assert "Problema di rete temporaneo durante la comunicazione con Telegram" in caplog.text
    assert not any(record.levelno == logging.ERROR for record in caplog.records)

    # Test con httpx.ReadError diretto
    caplog.clear()
    mock_context.error = httpx.ReadError("Connection closed")
    with caplog.at_level(logging.WARNING):
        await error_handler(None, mock_context)
    assert "Problema di rete temporaneo durante la comunicazione con Telegram" in caplog.text
    assert not any(record.levelno == logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_error_handler_unexpected_error(caplog):
    import logging
    from unittest.mock import MagicMock
    from bot.handlers import error_handler

    mock_context = MagicMock()
    mock_context.error = RuntimeError("Database connection failed")

    with caplog.at_level(logging.ERROR):
        await error_handler(None, mock_context)

    assert "Eccezione durante la gestione dell'aggiornamento:" in caplog.text
    assert any(record.levelno == logging.ERROR for record in caplog.records)

