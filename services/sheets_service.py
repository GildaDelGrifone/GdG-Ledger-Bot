import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Tuple
from config import settings
from core.models import Transaction

logger = logging.getLogger(__name__)


class SheetsServiceInterface(ABC):
    """
    Interfaccia astratta per il servizio di gestione del registro su foglio di calcolo
    """

    @abstractmethod
    def ensure_headers(self, worksheet_name: Optional[str] = None) -> None:
        pass

    @abstractmethod
    def get_last_transaction_id(self, worksheet_name: Optional[str] = None) -> int:
        pass

    @abstractmethod
    def get_last_box_money(self, worksheet_name: Optional[str] = None) -> float:
        pass

    @abstractmethod
    def get_last_receipt_number(self, worksheet_name: Optional[str] = None) -> int:
        pass

    @abstractmethod
    def add_transaction(self, transaction: Transaction, worksheet_name: Optional[str] = None) -> Transaction:
        pass

    @abstractmethod
    def link_satispay_id(self, transaction_id: int, satispay_id: str, worksheet_name: Optional[str] = None) -> bool:
        pass

    @abstractmethod
    def unlink_satispay_id(
        self,
        satispay_id: str,
        transaction_id: Optional[int] = None,
        worksheet_name: Optional[str] = None
    ) -> int:
        pass

    @abstractmethod
    def get_transaction_by_id(self, transaction_id: int, worksheet_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        pass


class MockSheetsService(SheetsServiceInterface):
    """
    Implementazione Mock in-memory per test locali e sviluppo senza credenziali Google Sheets
    """

    def __init__(self):
        self.sheets: Dict[str, List[List[Any]]] = {}
        logger.info("Inizializzato MockSheetsService (memoria locale)")

    def _get_sheet_data(self, worksheet_name: Optional[str] = None) -> List[List[Any]]:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        if name not in self.sheets:
            self.sheets[name] = [Transaction.sheet_headers()]
        return self.sheets[name]

    def ensure_headers(self, worksheet_name: Optional[str] = None) -> None:
        self._get_sheet_data(worksheet_name)

    def get_last_transaction_id(self, worksheet_name: Optional[str] = None) -> int:
        rows = self._get_sheet_data(worksheet_name)[1:]
        last_id = 0
        for row in rows:
            if len(row) > 0 and str(row[0]).strip().isdigit():
                val = int(row[0])
                if val > last_id:
                    last_id = val
        return last_id

    def get_last_box_money(self, worksheet_name: Optional[str] = None) -> float:
        rows = self._get_sheet_data(worksheet_name)[1:]
        for row in reversed(rows):
            if len(row) > 7 and row[7]:
                try:
                    cleaned = str(row[7]).replace("€", "").replace(",", ".").strip()
                    if cleaned:
                        return float(cleaned)
                except ValueError:
                    continue
        return 0.0

    def get_last_receipt_number(self, worksheet_name: Optional[str] = None) -> int:
        rows = self._get_sheet_data(worksheet_name)[1:]
        last_receipt = 0
        for row in rows:
            if len(row) > 8 and str(row[8]).strip().isdigit():
                val = int(row[8])
                if val > last_receipt:
                    last_receipt = val
        return last_receipt

    def add_transaction(self, transaction: Transaction, worksheet_name: Optional[str] = None) -> Transaction:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        sheet = self._get_sheet_data(name)
        new_id = self.get_last_transaction_id(name) + 1
        transaction.id = new_id
        sheet.append(transaction.to_sheet_row())
        logger.info(
            f"[MockSheets] Scrittura su foglio '{name}': Transazione ID #{new_id} registrata | "
            f"Operatore: @{transaction.telegram_username} (ID: {transaction.telegram_user_id}) | "
            f"Importo: {transaction.amount:.2f}€ | Metodo: {transaction.method} | "
            f"Flusso: {transaction.flow} | Causale: '{transaction.description}'"
        )
        return transaction

    def link_satispay_id(self, transaction_id: int, satispay_id: str, worksheet_name: Optional[str] = None) -> bool:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        sheet = self._get_sheet_data(name)
        for row in sheet[1:]:
            if len(row) > 0 and str(row[0]).strip() == str(transaction_id):
                while len(row) <= 11:
                    row.append("")
                row[11] = satispay_id
                logger.info(
                    f"[MockSheets] Scrittura su foglio '{name}': "
                    f"Collegato Satispay ID '{satispay_id}' alla transazione #{transaction_id}"
                )
                return True
        return False

    def unlink_satispay_id(
        self,
        satispay_id: str,
        transaction_id: Optional[int] = None,
        worksheet_name: Optional[str] = None
    ) -> int:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        sheet = self._get_sheet_data(name)
        unlinked_count = 0
        for row in sheet[1:]:
            if len(row) > 11 and row[11] == satispay_id:
                if transaction_id is None or str(row[0]).strip() == str(transaction_id):
                    row[11] = ""
                    unlinked_count += 1
        target_info = f"dalla transazione #{transaction_id}" if transaction_id is not None else "da tutte le transazioni collegate"
        logger.info(
            f"[MockSheets] Scrittura su foglio '{name}': "
            f"Scollegato Satispay ID '{satispay_id}' {target_info} (righe aggiornate: {unlinked_count})"
        )
        return unlinked_count

    def get_transaction_by_id(self, transaction_id: int, worksheet_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        sheet = self._get_sheet_data(worksheet_name)
        headers = sheet[0]
        for row in sheet[1:]:
            if len(row) > 0 and str(row[0]).strip() == str(transaction_id):
                return {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        return None


class GoogleSheetsService(SheetsServiceInterface):
    """
    Implementazione reale del servizio tramite API di Google Sheets e gspread
    con caching della cartella di lavoro e dei dati per massima velocità e reattività.
    """

    def __init__(self):
        import gspread
        self.gspread = gspread
        self.client = None
        self._spreadsheet = None
        self._worksheets: Dict[str, Any] = {}
        self._rows_cache: Dict[str, Tuple[float, List[List[Any]]]] = {}
        self._cache_ttl = 10.0  # Cache valida per 10 secondi per velocizzare step consecutivi
        self._init_client()

    def _init_client(self):
        if not settings.google_credentials_path.exists():
            raise FileNotFoundError(
                f"File credenziali Google non trovato in: {settings.GOOGLE_SERVICE_ACCOUNT_FILE}. "
                "Verifica il percorso o imposta USE_MOCK_SHEETS=true nel file .env."
            )
        self.client = self.gspread.service_account(filename=str(settings.google_credentials_path))
        logger.info("Client Google Sheets autenticato con successo")

    @staticmethod
    def _clean_spreadsheet_id(raw_id: str) -> str:
        raw_id = raw_id.strip()
        if "docs.google.com/spreadsheets/d/" in raw_id:
            return raw_id.split("/d/")[1].split("/")[0]
        return raw_id

    def _get_spreadsheet(self):
        if self._spreadsheet is None:
            spreadsheet_id = self._clean_spreadsheet_id(settings.GOOGLE_SPREADSHEET_ID)
            self._spreadsheet = self.client.open_by_key(spreadsheet_id)
        return self._spreadsheet

    def _get_worksheet(self, worksheet_name: Optional[str] = None):
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        if name in self._worksheets:
            return self._worksheets[name]

        spreadsheet = self._get_spreadsheet()
        try:
            ws = spreadsheet.worksheet(name)
        except self.gspread.exceptions.WorksheetNotFound:
            logger.info(f"Foglio '{name}' non trovato. Creazione nuovo foglio...")
            ws = spreadsheet.add_worksheet(title=name, rows=1000, cols=20)
            ws.append_row(Transaction.sheet_headers())

        self._worksheets[name] = ws
        return ws

    def _get_rows(self, worksheet_name: Optional[str] = None, force_refresh: bool = False) -> List[List[Any]]:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        now = time.time()
        if not force_refresh and name in self._rows_cache:
            cache_time, cached_rows = self._rows_cache[name]
            if now - cache_time < self._cache_ttl:
                return cached_rows

        ws = self._get_worksheet(name)
        try:
            all_values = ws.get_all_values()
        except Exception:
            # In caso di sessione scaduta, resetta i riferimenti e riprova
            self._worksheets.pop(name, None)
            self._spreadsheet = None
            ws = self._get_worksheet(name)
            all_values = ws.get_all_values()

        self._rows_cache[name] = (now, all_values)
        return all_values

    def _invalidate_cache(self, worksheet_name: Optional[str] = None):
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        self._rows_cache.pop(name, None)

    def ensure_headers(self, worksheet_name: Optional[str] = None) -> None:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        ws = self._get_worksheet(name)
        rows = self._get_rows(name)
        if not rows or not rows[0]:
            headers = Transaction.sheet_headers()
            ws.insert_row(headers, 1)
            self._invalidate_cache(name)
            logger.info(f"Intestazioni inserite nel foglio '{ws.title}'")

    def get_last_transaction_id(self, worksheet_name: Optional[str] = None) -> int:
        rows = self._get_rows(worksheet_name)[1:]
        last_id = 0
        for row in rows:
            if len(row) > 0 and str(row[0]).strip().isdigit():
                val = int(row[0])
                if val > last_id:
                    last_id = val
        return last_id

    def get_last_box_money(self, worksheet_name: Optional[str] = None) -> float:
        rows = self._get_rows(worksheet_name)[1:]
        for row in reversed(rows):
            if len(row) > 7 and row[7]:
                try:
                    cleaned = str(row[7]).replace("€", "").replace(",", ".").strip()
                    if cleaned:
                        return float(cleaned)
                except ValueError:
                    continue
        return 0.0

    def get_last_receipt_number(self, worksheet_name: Optional[str] = None) -> int:
        rows = self._get_rows(worksheet_name)[1:]
        last_receipt = 0
        for row in rows:
            if len(row) > 8 and str(row[8]).strip().isdigit():
                val = int(row[8])
                if val > last_receipt:
                    last_receipt = val
        return last_receipt

    def add_transaction(self, transaction: Transaction, worksheet_name: Optional[str] = None) -> Transaction:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        ws = self._get_worksheet(name)
        new_id = self.get_last_transaction_id(name) + 1
        transaction.id = new_id
        row_data = transaction.to_sheet_row()
        ws.append_row(row_data)

        # Aggiorna la cache locale per includere immediatamente la nuova riga
        if name in self._rows_cache:
            cache_time, cached_rows = self._rows_cache[name]
            cached_rows.append(row_data)
        else:
            self._invalidate_cache(name)

        logger.info(
            f"[GoogleSheets] Scrittura su foglio '{ws.title}': Transazione ID #{new_id} registrata con successo | "
            f"Operatore: @{transaction.telegram_username} (ID: {transaction.telegram_user_id}) | "
            f"Importo: {transaction.amount:.2f}€ | Metodo: {transaction.method} | "
            f"Flusso: {transaction.flow} | Causale: '{transaction.description}'"
        )
        return transaction

    def link_satispay_id(self, transaction_id: int, satispay_id: str, worksheet_name: Optional[str] = None) -> bool:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        rows = self._get_rows(name, force_refresh=True)
        target_row = None
        for idx, row in enumerate(rows[1:], start=2):
            if len(row) > 0 and str(row[0]).strip() == str(transaction_id):
                target_row = idx
                break

        if target_row is not None:
            ws = self._get_worksheet(name)
            ws.update_cell(target_row, 12, satispay_id)
            self._invalidate_cache(name)
            logger.info(
                f"[GoogleSheets] Scrittura su foglio '{ws.title}': "
                f"Collegato Satispay ID '{satispay_id}' alla riga {target_row} (Transazione ID #{transaction_id})"
            )
            return True
        logger.warning(
            f"[GoogleSheets] Tentativo collegamento fallito: Transazione ID #{transaction_id} non trovata nel foglio"
        )
        return False

    def unlink_satispay_id(
        self,
        satispay_id: str,
        transaction_id: Optional[int] = None,
        worksheet_name: Optional[str] = None
    ) -> int:
        name = worksheet_name or settings.GOOGLE_WORKSHEET_NAME
        rows = self._get_rows(name, force_refresh=True)
        ws = self._get_worksheet(name)
        unlinked_count = 0

        for idx, row in enumerate(rows[1:], start=2):
            if len(row) > 11 and row[11] == satispay_id:
                row_id = row[0] if len(row) > 0 else ""
                if transaction_id is None or str(row_id).strip() == str(transaction_id):
                    ws.update_cell(idx, 12, "")
                    unlinked_count += 1

        if unlinked_count > 0:
            self._invalidate_cache(name)

        target_info = f"dalla transazione #{transaction_id}" if transaction_id is not None else "da tutte le transazioni collegate"
        logger.info(
            f"[GoogleSheets] Scrittura su foglio '{ws.title}': "
            f"Scollegato Satispay ID '{satispay_id}' {target_info} (righe modificate: {unlinked_count})"
        )
        return unlinked_count

    def get_transaction_by_id(self, transaction_id: int, worksheet_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = self._get_rows(worksheet_name)
        if not rows:
            return None
        headers = rows[0]
        for row in rows[1:]:
            if len(row) > 0 and str(row[0]).strip() == str(transaction_id):
                return {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        return None


_sheets_service_instance: Optional[SheetsServiceInterface] = None


def get_sheets_service() -> SheetsServiceInterface:
    """
    Ritorna l'istanza configurata del servizio fogli di calcolo (Mock o Reale)
    """
    global _sheets_service_instance
    if _sheets_service_instance is None:
        if settings.USE_MOCK_SHEETS or not settings.GOOGLE_SPREADSHEET_ID or not settings.google_credentials_path.exists():
            logger.info("Utilizzo MockSheetsService (USE_MOCK_SHEETS=true o credenziali assenti)")
            _sheets_service_instance = MockSheetsService()
        else:
            try:
                _sheets_service_instance = GoogleSheetsService()
            except Exception as e:
                logger.error(f"Errore GoogleSheetsService: {e}. Ripiego su MockSheetsService.")
                _sheets_service_instance = MockSheetsService()
    return _sheets_service_instance

