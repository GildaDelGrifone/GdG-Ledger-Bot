import pytest
from core.models import Transaction
from services.sheets_service import MockSheetsService
from services.satispay_service import SatispayService


@pytest.fixture
def mock_sheets():
    service = MockSheetsService()
    service.ensure_headers("Transazioni")
    return service


@pytest.fixture
def sample_transaction():
    return Transaction(
        date_time="29/09/2026 20:00:00",
        method="Contanti",
        box_red=50.0,
        box_blue=20.0,
        description="Spesa cancelleria",
        flow="Uscita",
        amount=12.50,
        box_red_updated=50.0,
        box_blue_updated=7.50,
        receipt_number=1,
        telegram_username="mario_rossi",
        telegram_user_id=123456789,
        satispay_id=None,
    )
