from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from bot.handlers import (
    build_payment_links_keyboard,
    format_satispay_details,
    format_sumup_details,
    payment_details_callback,
)
from bot.keyboards import PROVIDER_SATISPAY, PROVIDER_SUMUP, get_payment_links_keyboard
from services.satispay_service import SatispayService
from services.sumup_service import SumUpService

SAT_ID = "01a1c1f0-c44f-7d45-ae1d-25b21a478366"


def test_satispay_dashboard_url():
    assert SatispayService.dashboard_url(SAT_ID) == (
        f"https://dashboard.satispay.com/dashboard/transactions/{SAT_ID}"
    )


@pytest.mark.asyncio
async def test_sumup_receipt_url(monkeypatch):
    monkeypatch.setattr(settings, "SUMUP_API_KEY", "sk_test")
    service = SumUpService()
    service._merchant_code = "MGP5G3JC"
    assert await service.receipt_url("TAAA7BN7GS4") == (
        "https://sales-receipt.sumup.com/pos/public/v1/MGP5G3JC/receipt/transaction:TAAA7BN7GS4?format=html"
    )


@pytest.mark.asyncio
async def test_sumup_receipt_url_without_merchant_code(monkeypatch):
    service = SumUpService()
    service._get_merchant_code = AsyncMock(return_value=None)
    monkeypatch.setattr(settings, "SUMUP_API_KEY", "sk_test")
    assert await service.receipt_url("TAAA7BN7GS4") is None


def test_payment_links_keyboard():
    kb = get_payment_links_keyboard(PROVIDER_SATISPAY, SAT_ID, "https://example.org/x")
    row = kb.inline_keyboard[0]
    assert row[0].url == "https://example.org/x"
    assert row[1].callback_data == f"txd:sat:{SAT_ID}"
    assert len(row[1].callback_data.encode()) <= 64

    # Senza URL resta solo il pulsante Dettagli
    kb = get_payment_links_keyboard(PROVIDER_SUMUP, "TAAA7BN7GS4", None)
    row = kb.inline_keyboard[0]
    assert len(row) == 1 and row[0].callback_data == "txd:sum:TAAA7BN7GS4"


@pytest.mark.asyncio
async def test_build_payment_links_keyboard_uses_receipt_url():
    with patch("bot.handlers.sumup_service.receipt_url", AsyncMock(return_value="https://r.example/T1")):
        kb = await build_payment_links_keyboard(PROVIDER_SUMUP, "T1")
    labels = [b.text for b in kb.inline_keyboard[0]]
    assert labels == ["🧾 Ricevuta SumUp", "🔍 Dettagli"]


def test_parse_item_reads_card_last4():
    tx = SumUpService._parse_item({
        "transaction_code": "T1", "amount": 20, "status": "SUCCESSFUL",
        "card": {"type": "VISA", "last_4_digits": "1554"},
    })
    assert tx.card_last4 == "1554"
    assert tx.card_type == "VISA"


def test_format_sumup_details():
    text = format_sumup_details({
        "transaction_code": "TAAA7BN7GS4", "amount": 20.0, "currency": "EUR", "status": "SUCCESSFUL",
        "card": {"type": "VISA", "last_4_digits": "1554"}, "entry_mode": "CONTACTLESS",
        "username": "a<b>@x.it", "timestamp": "2026-11-04T20:01:41.701Z", "tip_amount": 0,
    })
    assert "VISA •••• 1554" in text
    assert "20.00 EUR" in text
    assert "a&lt;b&gt;@x.it" in text
    assert "Mancia" not in text  # mancia a zero o assente non viene mostrata
    assert "04/11/2026" in text


def test_format_satispay_details():
    text = format_satispay_details({
        "id": SAT_ID, "amount_unit": 1050, "currency": "EUR", "status": "ACCEPTED",
        "sender": {"name": "Mario & Co"}, "insert_date": "2026-11-04T20:01:41.000Z",
    })
    assert "10.50 EUR" in text
    assert "Mario &amp; Co" in text
    assert SAT_ID in text
    assert "Note" not in text


def _make_callback_update(data: str):
    update = MagicMock()
    update.effective_chat.id = settings.TELEGRAM_ALLOWED_CHAT_ID
    update.effective_chat.send_message = AsyncMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.message_id = 42
    return update


@pytest.mark.asyncio
async def test_payment_details_callback_success():
    update = _make_callback_update("txd:sum:TAAA7BN7GS4")
    raw = {"transaction_code": "TAAA7BN7GS4", "amount": 20.0, "status": "SUCCESSFUL",
           "card": {"type": "VISA", "last_4_digits": "6455"}}
    with patch("bot.handlers.sumup_service.get_transaction_raw", AsyncMock(return_value=raw)), \
         patch("bot.handlers.sumup_service.receipt_url", AsyncMock(return_value="https://r.example/T")):
        await payment_details_callback(update, MagicMock())

    update.callback_query.answer.assert_awaited()
    args, kwargs = update.effective_chat.send_message.call_args
    assert "•••• 6455" in args[0]
    assert kwargs["reply_to_message_id"] == 42
    # Nella risposta resta solo il link alla ricevuta
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [b.url for b in buttons] == ["https://r.example/T"]


@pytest.mark.asyncio
async def test_payment_details_callback_api_error():
    update = _make_callback_update(f"txd:sat:{SAT_ID}")
    with patch("bot.handlers.satispay_service.get_payment_raw", AsyncMock(return_value=None)):
        await payment_details_callback(update, MagicMock())

    text = update.effective_chat.send_message.call_args.args[0]
    assert text.startswith("❌ Impossibile recuperare i dettagli Satispay")
