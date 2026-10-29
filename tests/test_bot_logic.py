import re
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
        "🔔 <b>Notifica Pagamento Satispay</b>\n\n"
        "📥 <b>Ricevuto pagamento da:</b> Mario Rossi\n"
        "💰 <b>Importo:</b> <code>+15.00 €</code>\n"
        "🆔 <b>ID Satispay:</b> <code>e8f52f8d-69f2-4e89-9a74-954f9a0c71bd</code>\n"
    )
    extracted = extract_satispay_id_from_text(sample_text)
    assert extracted == "e8f52f8d-69f2-4e89-9a74-954f9a0c71bd"

    sample_old_markdown = (
        "🔔 *Notifica Pagamento Satispay*\n\n"
        "📥 *Ricevuto pagamento da:* Mario Rossi\n"
        "💰 *Importo:* `+15.00 €`\n"
        "🆔 *ID Satispay:* `e8f52f8d-69f2-4e89-9a74-954f9a0c71bd`\n"
    )
    assert extract_satispay_id_from_text(sample_old_markdown) == "e8f52f8d-69f2-4e89-9a74-954f9a0c71bd"

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
    assert "<b>Notifica Pagamento Satispay</b>" in msg
    assert "/link" in msg
    assert "&lt;id_transazione&gt;" in msg


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

    # Utente con username (anche con underscore come @Big_Borzof)
    update_with_username = MagicMock()
    update_with_username.effective_user.username = "Big_Borzof"
    update_with_username.effective_user.first_name = "Big"
    update_with_username.effective_user.id = 111
    assert get_user_mention(update_with_username) == "👤 @Big_Borzof"

    # Utente senza username (link HTML)
    update_no_username = MagicMock()
    update_no_username.effective_user.username = None
    update_no_username.effective_user.first_name = "Luigi"
    update_no_username.effective_user.id = 222
    assert get_user_mention(update_no_username) == '👤 <a href="tg://user?id=222">Luigi</a>'

    # Utente con caratteri speciali nel nome
    update_special = MagicMock()
    update_special.effective_user.username = None
    update_special.effective_user.first_name = "Mario & Luigi <Brothers>"
    update_special.effective_user.id = 333
    assert get_user_mention(update_special) == '👤 <a href="tg://user?id=333">Mario &amp; Luigi &lt;Brothers&gt;</a>'

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
async def test_send_msg_with_underscore_username():
    from unittest.mock import AsyncMock, MagicMock
    from bot.conversation import send_msg

    update = MagicMock()
    update.effective_user.username = "Big_Borzof"
    update.effective_user.first_name = "Big"
    update.effective_user.id = 123456
    update.effective_chat.send_message = AsyncMock()
    update.callback_query = None

    await send_msg(update, "📅 <b>Data e Ora della transazione:</b>\nPremi il pulsante...", reply_markup=None)

    update.effective_chat.send_message.assert_called_once()
    call_kwargs = update.effective_chat.send_message.call_args.kwargs
    assert call_kwargs["parse_mode"] == "HTML"
    assert "👤 @Big_Borzof" in call_kwargs["text"]
    assert "<b>Data e Ora della transazione:</b>" in call_kwargs["text"]


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



@pytest.mark.asyncio
async def test_satispay_box_money_flow():
    from unittest.mock import AsyncMock, MagicMock, patch
    from telegram.ext import ConversationHandler
    from bot.conversation import advance_or_finish, STATE_BOX_BEFORE
    from services.sheets_service import MockSheetsService

    mock_sheets = MockSheetsService()
    mock_sheets.get_last_box_balances = MagicMock(return_value=(120.50, 30.00))

    with patch("bot.conversation.get_sheets_service", return_value=mock_sheets):
        update = MagicMock()
        update.effective_chat.send_message = AsyncMock()
        update.callback_query = None
        update.effective_user.username = "test_user"
        update.effective_user.id = 123

        context = MagicMock()
        context.user_data = {
            "tx_data": {
                "date_time": "08/10/2026 18:00:00",
                "method": "Satispay",
            }
        }

        # Step 3: Should ask for box confirmation with Satispay specific message
        next_state = await advance_or_finish(update, context)
        assert next_state == STATE_BOX_BEFORE
        sent_text = update.effective_chat.send_message.call_args.kwargs["text"]
        assert "Conferma saldi delle casse attuali" in sent_text
        assert "120.50 €" in sent_text and "30.00 €" in sent_text

        # Now simulate user input for the balances (via button or text)
        context.user_data["tx_data"]["box_red"] = 120.50
        context.user_data["tx_data"]["box_blue"] = 30.00
        context.user_data["tx_data"]["description"] = "Test Satispay Spesa"
        context.user_data["tx_data"]["flow"] = "Uscita"
        context.user_data["tx_data"]["amount"] = 15.00
        context.user_data["tx_data"]["receipt_number"] = 0

        # Step 7 & Finalize: Satispay automatically copies box_money to box_money_updated
        final_state = await advance_or_finish(update, context)
        assert final_state == ConversationHandler.END

        # Con Satispay i saldi aggiornati (I/J) coincidono con quelli iniziali (D/E)
        row = mock_sheets._get_sheet_data()[1]
        assert row[3] == "120.50" and row[4] == "30.00"
        assert row[8] == "120.50" and row[9] == "30.00"

        summary_call = update.effective_chat.send_message.call_args_list[-1]
        summary_text = summary_call.kwargs["text"]
        assert "Cassa Rossa" in summary_text and "Cassa Blu" in summary_text
        assert "120.50 €" in summary_text



def test_extract_payment_ref():
    from bot.handlers import extract_payment_ref, format_sumup_echo
    from core.models import SumUpTransaction

    tx = SumUpTransaction(transaction_code="TEENSK4W2K", amount=12.5, status="SUCCESSFUL", card_type="VISA")
    # Telegram restituisce il testo senza tag HTML quando si risponde a un messaggio
    plain = re.sub(r"<[^>]+>", "", format_sumup_echo(tx))
    assert extract_payment_ref(plain) == ("sumup", "TEENSK4W2K")
    assert extract_payment_ref("ID Satispay: sat_custom_123") == ("satispay", "sat_custom_123")
    assert extract_payment_ref("niente da vedere") == (None, None)


@pytest.mark.asyncio
async def test_sumup_method_aligns_box_money_and_ask_keyboard(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch
    from telegram.ext import ConversationHandler
    from bot.conversation import advance_or_finish, STATE_SUMUP_ASK
    from config import settings
    from services.sheets_service import MockSheetsService

    mock_sheets = MockSheetsService()
    with patch("bot.conversation.get_sheets_service", return_value=mock_sheets):
        update = MagicMock()
        update.effective_chat.send_message = AsyncMock()
        update.callback_query = None
        update.effective_user.username = "test_user"
        update.effective_user.id = 123

        base = {
            "date_time": "08/10/2026 18:00:00", "method": "SumUp", "box_red": 50.0, "box_blue": 10.0,
            "description": "Test SumUp", "flow": "Entrata", "amount": 15.0, "receipt_number": 0,
        }

        # QR abilitato: il prompt offre terminale + link
        monkeypatch.setattr(settings, "SUMUP_QR_ENABLED", True)
        context = MagicMock()
        context.user_data = {"tx_data": dict(base)}
        assert await advance_or_finish(update, context) == STATE_SUMUP_ASK
        kb = update.effective_chat.send_message.call_args.kwargs["reply_markup"]
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert any("QR link" in l for l in labels)

        # QR disabilitato: il pulsante del link non compare
        monkeypatch.setattr(settings, "SUMUP_QR_ENABLED", False)
        context = MagicMock()
        context.user_data = {"tx_data": dict(base)}
        assert await advance_or_finish(update, context) == STATE_SUMUP_ASK
        kb = update.effective_chat.send_message.call_args.kwargs["reply_markup"]
        labels = [b.text for row in kb.inline_keyboard for b in row]
        assert not any("QR link" in l for l in labels)
        assert any("terminale" in l for l in labels)

        # Scelta "salta": registra con cassa D/H allineata
        context.user_data["tx_data"]["sumup_choice_done"] = True
        assert await advance_or_finish(update, context) == ConversationHandler.END
        row = mock_sheets._get_sheet_data()[1]
        assert row[2] == "SumUp"
        assert row[3] == "50.00" and row[4] == "10.00"
        assert row[8] == "50.00" and row[9] == "10.00"


@pytest.mark.asyncio
async def test_sumup_skip_while_waiting_registers_without_id():
    from unittest.mock import AsyncMock, MagicMock, patch
    from telegram.ext import ConversationHandler
    from bot.conversation import handle_qr_waiting_input
    from bot.keyboards import CALLBACK_QR_SKIP
    from services.sheets_service import MockSheetsService

    mock_sheets = MockSheetsService()
    with patch("bot.conversation.get_sheets_service", return_value=mock_sheets):
        update = MagicMock()
        update.effective_chat.send_message = AsyncMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.data = CALLBACK_QR_SKIP
        update.effective_user.username = "test_user"
        update.effective_user.id = 123

        context = MagicMock()
        context.user_data = {"tx_data": {
            "date_time": "08/10/2026 18:00:00", "method": "SumUp", "box_red": 50.0, "box_blue": 10.0,
            "description": "Test", "flow": "Entrata", "amount": 5.0, "receipt_number": 0,
        }}

        assert await handle_qr_waiting_input(update, context) == ConversationHandler.END
        row = mock_sheets._get_sheet_data()[1]
        assert row[7] == "5.00"   # importo inserito dall'operatore
        assert row[15] == ""      # nessun ID SumUp


def test_parse_two_amounts():
    from bot.conversation import parse_two_amounts

    assert parse_two_amounts("120 35,50") == (120.0, 35.5)
    assert parse_two_amounts("120/35.5") == (120.0, 35.5)
    assert parse_two_amounts("€ 10 € 5") == (10.0, 5.0)
    assert parse_two_amounts("120") is None
    assert parse_two_amounts("a b") is None
    assert parse_two_amounts("1 2 3") is None


def _make_update():
    from unittest.mock import AsyncMock, MagicMock

    update = MagicMock()
    update.effective_chat.send_message = AsyncMock()
    update.effective_chat.id = settings.TELEGRAM_ALLOWED_CHAT_ID
    update.callback_query = None
    update.effective_user.username = "test_user"
    update.effective_user.id = 123
    return update


@pytest.mark.asyncio
async def test_cash_transaction_moves_only_blue_box():
    from unittest.mock import MagicMock, patch
    from telegram.ext import ConversationHandler
    from bot.conversation import advance_or_finish, STATE_BOX_AFTER, handle_box_after_input
    from bot.keyboards import CALLBACK_BOX_CONFIRM_CALCULATED
    from services.sheets_service import MockSheetsService

    mock_sheets = MockSheetsService()
    with patch("bot.conversation.get_sheets_service", return_value=mock_sheets):
        update = _make_update()
        context = MagicMock()
        context.user_data = {"tx_data": {
            "date_time": "08/10/2026 18:00:00", "method": "Contanti", "box_red": 100.0, "box_blue": 20.0,
            "description": "Incasso", "flow": "Entrata", "amount": 10.0, "receipt_number": 0,
        }}

        # Il bot propone il saldo calcolato della sola Cassa Blu
        assert await advance_or_finish(update, context) == STATE_BOX_AFTER
        assert context.user_data["suggested_box_after"] == 30.0

        # L'utente conferma il saldo calcolato
        from unittest.mock import AsyncMock
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.data = CALLBACK_BOX_CONFIRM_CALCULATED
        assert await handle_box_after_input(update, context) == ConversationHandler.END

        row = mock_sheets._get_sheet_data()[1]
        assert (row[3], row[4]) == ("100.00", "20.00")
        assert (row[8], row[9]) == ("100.00", "30.00")
        assert mock_sheets.get_last_box_balances() == (100.0, 30.0)


@pytest.mark.asyncio
async def test_transfer_blue_to_red_and_red_to_blue():
    from unittest.mock import MagicMock, patch
    from telegram.ext import ConversationHandler
    from bot.conversation import (
        start_transfer, advance_or_finish, handle_transfer_direction_input,
        handle_box_before_input, handle_amount_input, STATE_TRANSFER_DIRECTION, STATE_BOX_BEFORE,
    )
    from bot.keyboards import CALLBACK_TRANSFER_BLUE_TO_RED, CALLBACK_TRANSFER_RED_TO_BLUE
    from unittest.mock import AsyncMock
    from services.sheets_service import MockSheetsService

    mock_sheets = MockSheetsService()
    with patch("bot.conversation.get_sheets_service", return_value=mock_sheets):
        for callback, start_balances, amount, expected_after, expected_desc in [
            (CALLBACK_TRANSFER_BLUE_TO_RED, "100 30", "20", ("120.00", "10.00"), "Trasferimento Blu → Rossa"),
            (CALLBACK_TRANSFER_RED_TO_BLUE, "100 30", "40", ("60.00", "70.00"), "Trasferimento Rossa → Blu"),
        ]:
            update = _make_update()
            context = MagicMock()
            context.user_data = {}

            assert await start_transfer(update, context) == STATE_TRANSFER_DIRECTION

            update.callback_query = MagicMock()
            update.callback_query.answer = AsyncMock()
            update.callback_query.data = callback
            assert await handle_transfer_direction_input(update, context) == STATE_BOX_BEFORE

            update.callback_query = None
            update.message.text = start_balances
            from bot.conversation import STATE_AMOUNT
            assert await handle_box_before_input(update, context) == STATE_AMOUNT

            update.message.text = amount
            assert await handle_amount_input(update, context) == ConversationHandler.END

            row = mock_sheets._get_sheet_data()[-1]
            assert row[2] == "Trasferimento" and row[6] == "Trasferimento"
            assert row[5] == expected_desc
            assert row[7] == f"{float(amount):.2f}"
            assert (row[8], row[9]) == expected_after
            assert row[10] == 0  # nessuna ricevuta


@pytest.mark.asyncio
async def test_transfer_rejects_amount_above_source_balance():
    from unittest.mock import MagicMock, patch
    from bot.conversation import handle_amount_input, STATE_AMOUNT

    update = _make_update()
    update.message.text = "50"
    context = MagicMock()
    context.user_data = {"tx_data": {
        "kind": "transfer", "transfer_direction": "blue_to_red", "box_red": 500.0, "box_blue": 30.0,
    }}

    assert await handle_amount_input(update, context) == STATE_AMOUNT
    assert "amount" not in context.user_data["tx_data"]
    assert "Saldo insufficiente" in update.effective_chat.send_message.call_args.kwargs["text"]
