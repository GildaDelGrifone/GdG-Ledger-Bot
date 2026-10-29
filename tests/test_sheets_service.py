import pytest
from core.models import Transaction
from services.sheets_service import MockSheetsService


def test_ensure_headers(mock_sheets: MockSheetsService):
    headers = mock_sheets._get_sheet_data("Transazioni")[0]
    assert "ID" in headers
    assert "Data e Ora" in headers
    assert "Metodo" in headers
    assert "Cassa Rossa Iniziale (€)" in headers
    assert "Cassa Blu Iniziale (€)" in headers
    assert "Descrizione" in headers
    assert "Flusso" in headers
    assert "Importo (€)" in headers
    assert "Cassa Rossa Aggiornata (€)" in headers
    assert "Cassa Blu Aggiornata (€)" in headers
    assert "Numero Ricevuta" in headers
    assert "Operatore" in headers
    assert "Telegram ID" in headers
    assert "ID Satispay" in headers
    assert headers[-1] == "ID SumUp"
    assert len(headers) == 16


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
        box_red=65.50,
        box_blue=12.00,
        description="Quota socio",
        flow="Entrata",
        amount=20.0,
        box_red_updated=65.50,
        box_blue_updated=12.00,
        receipt_number=1,
        telegram_username="giovanni_bianchi",
        telegram_user_id=11223344
    )
    res = mock_sheets.add_transaction(tx_satispay, "Transazioni")
    assert res.id is not None
    row = mock_sheets._get_sheet_data("Transazioni")[1]
    headers = mock_sheets._get_sheet_data("Transazioni")[0]

    # Colonne D/E (iniziali) e I/J (aggiornate) per Cassa Rossa e Cassa Blu
    assert headers[3] == "Cassa Rossa Iniziale (€)"
    assert headers[4] == "Cassa Blu Iniziale (€)"
    assert headers[8] == "Cassa Rossa Aggiornata (€)"
    assert headers[9] == "Cassa Blu Aggiornata (€)"

    assert row[3] == "65.50" and row[4] == "12.00"
    assert row[8] == "65.50" and row[9] == "12.00"

    assert mock_sheets.get_last_box_balances("Transazioni") == (65.50, 12.00)


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
        box_red=100.0,
        box_blue=0.0,
        description="Test conversione data",
        flow="Entrata",
        amount=50.0,
        box_red_updated=100.0,
        box_blue_updated=50.0,
        receipt_number=10,
        telegram_username="mario",
        telegram_user_id=123,
        created_at="2026-10-08 14:35:22"
    )

    # Both date_time and created_at must be formatted as DD/MM/YYYY HH.MM.SS
    assert tx.date_time == "08/10/2026 14:30:00"
    assert tx.created_at == "08/10/2026 14:35:22"

    row = tx.to_sheet_row()
    # Column B (index 1) is Data e Ora
    assert row[1] == "08/10/2026 14:30:00"
    # Column O (index 14) is Data Registrazione
    assert row[14] == "08/10/2026 14:35:22"

    saved = mock_sheets.add_transaction(tx, "Transazioni")
    sheet_data = mock_sheets._get_sheet_data("Transazioni")[1]
    assert sheet_data[1] == "08/10/2026 14:30:00"
    assert sheet_data[14] == "08/10/2026 14:35:22"



def test_get_last_box_balances(mock_sheets: MockSheetsService, sample_transaction: Transaction):
    assert mock_sheets.get_last_box_balances("Transazioni") == (0.0, 0.0)

    mock_sheets.add_transaction(sample_transaction, "Transazioni")
    assert mock_sheets.get_last_box_balances("Transazioni") == (50.0, 7.50)


def test_get_last_box_balances_searches_each_box_independently():
    from services.sheets_service import _last_box_balances

    # Vecchio layout migrato: la Cassa Blu è vuota nelle righe storiche
    rows = [
        ["1", "d", "Contanti", "10", "", "x", "Entrata", "5", "15,50", "", "1"],
        ["2", "d", "Contanti", "15", "2", "x", "Entrata", "5", "", "7", "2"],
    ]
    assert _last_box_balances(rows) == (15.5, 7.0)


def test_migrate_old_layout_to_two_boxes():
    from services.sheets_service import GoogleSheetsService

    class FakeWorksheet:
        title = "Transazioni"

        def __init__(self, rows):
            self.rows = rows

        def get_all_values(self):
            return [list(r) for r in self.rows]

        def insert_cols(self, values, col=1):
            for i, row in enumerate(self.rows):
                row.insert(col - 1, values[0][i] if i < len(values[0]) else "")

        def update_cell(self, r, c, value):
            row = self.rows[r - 1]
            while len(row) < c:
                row.append("")
            row[c - 1] = value

    old_header = [
        "ID", "Data e Ora", "Metodo", "Cassa Iniziale (€)", "Descrizione", "Flusso", "Importo (€)",
        "Cassa Aggiornata (€)", "Numero Ricevuta", "Operatore", "Telegram ID", "ID Satispay", "Data Registrazione",
    ]
    old_row = ["1", "01/01/2026", "Contanti", "50.00", "Spesa", "Uscita", "5.00", "45.00", "3", "u", "9", "sat-1", "01/01/2026"]
    ws = FakeWorksheet([old_header, old_row])

    service = object.__new__(GoogleSheetsService)
    service._worksheets = {"Transazioni": ws}
    service._rows_cache = {}
    service._cache_ttl = 10.0
    service._spreadsheet = None

    service.ensure_headers("Transazioni")

    header, row = ws.rows
    assert header[3] == "Cassa Rossa Iniziale (€)" and header[4] == "Cassa Blu Iniziale (€)"
    assert header[8] == "Cassa Rossa Aggiornata (€)" and header[9] == "Cassa Blu Aggiornata (€)"
    assert header[13] == "ID Satispay" and header[15] == "ID SumUp"
    # I dati restano allineati: saldo rosso storico, blu vuoto, ricevuta e ID Satispay negli indici nuovi
    assert row[3] == "50.00" and row[4] == "" and row[8] == "45.00" and row[9] == ""
    assert row[10] == "3" and row[13] == "sat-1"


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


def test_link_and_unlink_sumup_id(mock_sheets: MockSheetsService, sample_transaction: Transaction):
    tx1 = mock_sheets.add_transaction(sample_transaction, "Transazioni")

    assert mock_sheets.link_sumup_id(tx1.id, "TEENSK4W2K", "Transazioni") is True
    record = mock_sheets.get_transaction_by_id(tx1.id, "Transazioni")
    assert record["ID SumUp"] == "TEENSK4W2K"
    assert record["ID Satispay"] == ""

    assert mock_sheets.unlink_sumup_id("TEENSK4W2K", transaction_id=tx1.id, worksheet_name="Transazioni") == 1
    assert mock_sheets.get_transaction_by_id(tx1.id, "Transazioni")["ID SumUp"] == ""


def test_sumup_id_in_row(mock_sheets: MockSheetsService):
    tx = Transaction(
        date_time="29/09/2026", method="SumUp", description="A", flow="Entrata",
        amount=10.0, receipt_number=1, telegram_username="u", telegram_user_id=1,
        sumup_id="CODE123"
    )
    mock_sheets.add_transaction(tx)
    record = mock_sheets.get_transaction_by_id(tx.id)
    assert record["ID SumUp"] == "CODE123"
