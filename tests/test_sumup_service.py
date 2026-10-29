import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from core.models import SumUpTransaction
from services.db_service import DatabaseService
from services.sumup_service import SumUpService


def _ts(delta_seconds: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _tx(code: str, amount: float = 10.0, status: str = "SUCCESSFUL", type_: str = "PAYMENT", ts: str = None):
    return SumUpTransaction(
        id=f"id-{code}", transaction_code=code, amount=amount, status=status, type=type_,
        payment_type="POS", card_type="VISA", timestamp=ts or _ts(),
    )


def test_parse_item():
    item = {
        "id": "abc", "transaction_code": "TEENSK4W2K", "amount": 12.5, "currency": "EUR",
        "status": "SUCCESSFUL", "type": "PAYMENT", "payment_type": "POS", "card_type": "MASTERCARD",
        "timestamp": "2026-10-29T10:00:00.123Z", "user": "a@b.it",
    }
    tx = SumUpService._parse_item(item)
    assert tx.transaction_code == "TEENSK4W2K"
    assert tx.amount_euro == 12.5
    assert tx.card_type == "MASTERCARD"


@pytest.mark.asyncio
async def test_poll_new_transactions_behavior(monkeypatch):
    service = SumUpService()

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        test_db = DatabaseService(tmp.name)
        monkeypatch.setattr("services.db_service.db_service", test_db)

        t1, t2 = _tx("T1", ts=_ts(-20)), _tx("T2", ts=_ts(-10))
        service.get_transactions_history = AsyncMock(return_value=[t2, t1])

        # Primo avvio: sincronizzazione silenziosa
        assert await service.poll_new_transactions() == []
        assert test_db.has_sumup_transaction("T1") and test_db.has_sumup_transaction("T2")

        # Nessuna novità
        assert await service.poll_new_transactions() == []

        # Nuova transazione riuscita + una fallita (salvata ma non notificata)
        t3 = _tx("T3", ts=_ts(-5))
        t4 = _tx("T4", status="FAILED", ts=_ts(-2))
        service.get_transactions_history = AsyncMock(return_value=[t4, t3, t2, t1])
        new = await service.poll_new_transactions()
        assert [t.transaction_code for t in new] == ["T3"]
        assert test_db.has_sumup_transaction("T4")


@pytest.mark.asyncio
async def test_find_new_payment_ignores_amount():
    service = SumUpService()
    since = datetime.now(timezone.utc)

    history = [
        _tx("FAILED1", 10.0, status="FAILED", ts=_ts(5)),
        _tx("REFUND1", 10.0, type_="REFUND", ts=_ts(4)),
        _tx("OLD", 10.0, ts=_ts(-600)),
        _tx("TAKEN", 10.0, ts=_ts(2)),
        _tx("OTHER", 11.0, ts=_ts(3)),
        _tx("FIRST", 99.0, ts=_ts(1)),
    ]
    service.get_transactions_history = AsyncMock(return_value=history)

    # Importo diverso da quello inserito: viene collegata comunque la prima transazione utile
    match = await service.find_new_payment(since, exclude_codes={"TAKEN"})
    assert match is not None and match.transaction_code == "FIRST"

    service.get_transactions_history = AsyncMock(return_value=history[:3])
    assert await service.find_new_payment(since) is None


def test_checkout_result():
    paid = {"status": "PAID", "transactions": [{"transaction_code": "TX9", "status": "SUCCESSFUL"}]}
    assert SumUpService.checkout_result(paid) == ("PAID", "TX9")
    assert SumUpService.checkout_result({"status": "EXPIRED"}) == ("FAILED", "EXPIRED")
    assert SumUpService.checkout_result({"status": "PENDING"}) is None


def test_db_sumup_queries():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = DatabaseService(tmp.name)
        assert db.sumup_is_empty()
        assert db.save_sumup_transaction(_tx("A", 5.0, ts="2026-09-30T10:00:00.000Z")) is True
        assert db.save_sumup_transaction(_tx("A", 5.0, ts="2026-09-30T10:00:00.000Z")) is False
        db.save_sumup_transaction(_tx("B", 7.0, ts="2026-10-02T10:00:00.000Z"))

        assert db.get_sumup_by_code("A").amount == 5.0
        assert [t.transaction_code for t in db.get_sumup_by_date("30/09/2026")] == ["A"]
        assert len(db.get_sumup_by_range("01/09/2026", "30/10/2026")) == 2
        assert db.get_recent_sumup(1)[0].transaction_code == "B"
