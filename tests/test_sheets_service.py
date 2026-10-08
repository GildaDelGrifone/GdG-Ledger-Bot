import pytest
from core.models import Transaction
from services.sheets_service import MockSheetsService


def test_ensure_headers(mock_sheets: MockSheetsService):
    headers = mock_sheets._get_sheet_data("Transazioni")[0]
    assert "ID" in headers
    assert "Data e Ora" in headers
    assert "Metodo" in headers
    assert "Cassa Iniziale (€)" in headers
    assert "Descrizione" in headers
    assert "Flusso" in headers
    assert "Importo (€)" in headers
    assert "Cassa Aggiornata (€)" in headers
    assert "Numero Ricevuta" in headers
    assert "Operatore" in headers
    assert "Telegram ID" in headers
    assert "ID Satispay" in headers


def test_add_transaction_increments_id(mock_sheets: MockSheetsService, sample_transaction: Transaction):
    assert mock_sheets.get_last_transaction_id("Transazioni") == 0

    tx1 = mock_sheets.add_transaction(sample_transaction, "Transazioni")
    assert tx1.id == 1
    assert mock_sheets.get_last_transaction_id("Transazioni") == 1

    tx2 = Transaction(
        date_time="29/09/2026 21:00:00",
        method="Satispay",
        description="Quota",
        flow="Entrata",
        amount=25.0,
        receipt_number=2,
        telegram_username="luigi_verdi",
        telegram_user_id=987654321
    )
    res2 = mock_sheets.add_transaction(tx2, "Transazioni")
    assert res2.id == 2
    assert mock_sheets.get_last_transaction_id("Transazioni") == 2

def test_satispay_transaction_box_columns(mock_sheets: MockSheetsService):
    tx_satispay = Transaction(
        date_time="08/10/2026 18:30:00",
        method="Satispay",
        box_money=65.50,
        description="Quota socio",
        flow="Entrata",
        amount=20.0,
        box_money_updated=65.50,
        receipt_number=1,
        telegram_username="giovanni_bianchi",
        telegram_user_id=11223344
    )
    res = mock_sheets.add_transaction(tx_satispay, "Transazioni")
    assert res.id is not None
    row = mock_sheets._get_sheet_data("Transazioni")[1]
    headers = mock_sheets._get_sheet_data("Transazioni")[0]

    # Verify column headers correspond to index 3 and 7
    assert headers[3] == "Cassa Iniziale (€)"
    assert headers[7] == "Cassa Aggiornata (€)"

    # Verify both columns have the exact confirmed value
    assert row[3] == "65.50"
    assert row[7] == "65.50"

    # Also verify get_last_box_money returns this value as latest
    assert mock_sheets.get_last_box_money("Transazioni") == 65.50


def test_format_datetime_for_sheet_helper():
    from core.models import format_datetime_for_sheet

    assert format_datetime_for_sheet("2026-10-08 14:30:15") == "08/10/2026 14:30:15"
    assert format_datetime_for_sheet("2026-10-08 14:30") == "08/10/2026 14:30:00"
    assert format_datetime_for_sheet("2026-10-08") == "08/10/2026 00:00:00"
    assert format_datetime_for_sheet("08/10/2026 14:30:15") == "08/10/2026 14:30:15"
    assert format_datetime_for_sheet("08/10/2026 14:30") == "08/10/2026 14:30:00"
    assert format_datetime_for_sheet("08/10/2026") == "08/10/2026 00:00:00"
    assert format_datetime_for_sheet("2026-10-08T14:30:15Z") == "08/10/2026 14:30:15"
    assert format_datetime_for_sheet("") == ""
    assert format_datetime_for_sheet(None) == ""


def test_transaction_date_format_saved_in_sheet(mock_sheets: MockSheetsService):
    tx = Transaction(
        date_time="2026-10-08 14:30:00",
        method="Contanti",
        box_money=100.0,
        description="Test conversione data",
        flow="Entrata",
        amount=50.0,
        box_money_updated=150.0,
        receipt_number=10,
        telegram_username="mario",
        telegram_user_id=123,
        created_at="2026-10-08 14:35:22"
    )

    # Both date_time and created_at must be formatted as DD/MM/YYYY HH:MM:SS
    assert tx.date_time == "08/10/2026 14:30:00"
    assert tx.created_at == "08/10/2026 14:35:22"

    row = tx.to_sheet_row()
    # Column B (index 1) is Data e Ora
    assert row[1] == "08/10/2026 14:30:00"
    # Column M (index 12) is Data Registrazione
    assert row[12] == "08/10/2026 14:35:22"

    saved = mock_sheets.add_transaction(tx, "Transazioni")
    sheet_data = mock_sheets._get_sheet_data("Transazioni")[1]
    assert sheet_data[1] == "08/10/2026 14:30:00"
    assert sheet_data[12] == "08/10/2026 14:35:22"



def test_get_last_box_money(mock_sheets: MockSheetsService, sample_transaction: Transaction):
    assert mock_sheets.get_last_box_money("Transazioni") == 0.0

    mock_sheets.add_transaction(sample_transaction, "Transazioni")
    assert mock_sheets.get_last_box_money("Transazioni") == 37.50


def test_get_last_receipt_number(mock_sheets: MockSheetsService, sample_transaction: Transaction):
    assert mock_sheets.get_last_receipt_number("Transazioni") == 0

    mock_sheets.add_transaction(sample_transaction, "Transazioni")
    assert mock_sheets.get_last_receipt_number("Transazioni") == 1


def test_link_and_unlink_satispay_id(mock_sheets: MockSheetsService, sample_transaction: Transaction):
    tx1 = mock_sheets.add_transaction(sample_transaction, "Transazioni")
    satispay_uuid = "sat-uuid-999"

    # Collega ID
    ok = mock_sheets.link_satispay_id(tx1.id, satispay_uuid, "Transazioni")
    assert ok is True

    record = mock_sheets.get_transaction_by_id(tx1.id, "Transazioni")
    assert record is not None
    assert record["ID Satispay"] == satispay_uuid

    # Scollega ID per specifica transazione
    unlinked = mock_sheets.unlink_satispay_id(satispay_uuid, transaction_id=tx1.id, worksheet_name="Transazioni")
    assert unlinked == 1

    record_after = mock_sheets.get_transaction_by_id(tx1.id, "Transazioni")
    assert record_after["ID Satispay"] == ""


def test_unlink_all_satispay_ids(mock_sheets: MockSheetsService):
    tx1 = Transaction(
        date_time="29/09/2026", method="Satispay", description="A", flow="Entrata",
        amount=10.0, receipt_number=1, telegram_username="u", telegram_user_id=1,
        satispay_id="shared-sat-id"
    )
    tx2 = Transaction(
        date_time="29/09/2026", method="Satispay", description="B", flow="Entrata",
        amount=20.0, receipt_number=2, telegram_username="u", telegram_user_id=1,
        satispay_id="shared-sat-id"
    )
    mock_sheets.add_transaction(tx1)
    mock_sheets.add_transaction(tx2)

    # Scollega da tutte le transazioni
    unlinked = mock_sheets.unlink_satispay_id("shared-sat-id")
    assert unlinked == 2
