from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field, field_validator


def format_datetime_for_sheet(dt_val: Optional[str]) -> str:
    """
    Converte una data/ora in formato standard DD/MM/YYYY HH.MM.SS per Google Sheets.
    Supporta conversioni da YYYY-MM-DD HH.MM.SS, YYYY-MM-DD HH:MM, ISO format, ecc.
    """
    if not dt_val:
        return ""
    val = dt_val.strip()

    formats = [
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(val, fmt)
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            continue
    return val


class Transaction(BaseModel):
    """
    Modello di una transazione finanziaria per il registro
    """
    id: Optional[int] = None
    date_time: str
    method: str  # "Contanti" oppure "Satispay"
    box_money: Optional[float] = None  # Cassa iniziale prima della transazione (o saldo confermato per Satispay)
    description: str
    flow: str  # "Entrata" oppure "Uscita"
    amount: float
    box_money_updated: Optional[float] = None  # Cassa aggiornata (o saldo confermato per Satispay)
    receipt_number: int  # Numero progressivo ricevuta (0 se non applicabile)
    telegram_username: str
    telegram_user_id: int
    satispay_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

    @field_validator("date_time", "created_at", mode="before")
    @classmethod
    def validate_datetimes(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            return v.strftime("%d/%m/%Y %H:%M:%S")
        if isinstance(v, str):
            return format_datetime_for_sheet(v)
        return v

    def to_sheet_row(self) -> List[Any]:
        """
        Converte la transazione in una lista di valori per la riga del foglio di calcolo
        """
        return [
            self.id if self.id is not None else "",
            format_datetime_for_sheet(self.date_time),
            self.method,
            f"{self.box_money:.2f}" if self.box_money is not None else "",
            self.description,
            self.flow,
            f"{self.amount:.2f}",
            f"{self.box_money_updated:.2f}" if self.box_money_updated is not None else "",
            self.receipt_number,
            self.telegram_username,
            str(self.telegram_user_id),
            self.satispay_id or "",
            format_datetime_for_sheet(self.created_at)
        ]

    @classmethod
    def sheet_headers(cls) -> List[str]:
        """
        Intestazioni delle colonne nel foglio di calcolo
        """
        return [
            "ID",
            "Data e Ora",
            "Metodo",
            "Cassa Iniziale (€)",
            "Descrizione",
            "Flusso",
            "Importo (€)",
            "Cassa Aggiornata (€)",
            "Numero Ricevuta",
            "Operatore",
            "Telegram ID",
            "ID Satispay",
            "Data Registrazione"
        ]


class CustomCommandDefaults(BaseModel):
    """
    Valori predefiniti configurabili per un comando personalizzato
    """
    date_time: Optional[str] = None  # es: "now"
    method: Optional[str] = None     # es: "Contanti" o "Satispay"
    flow: Optional[str] = None       # es: "Entrata" o "Uscita"
    amount: Optional[float] = None   # es: 1.50
    description: Optional[str] = None
    description_prefix: Optional[str] = None
    description_suffix: Optional[str] = None
    box_money: Optional[float] = None


class CustomCommandConfig(BaseModel):
    """
    Configurazione di un comando personalizzato da file YAML/JSON
    """
    command: str
    description: str
    defaults: CustomCommandDefaults = Field(default_factory=CustomCommandDefaults)


class SatispayPayment(BaseModel):
    """
    Modello per una notifica/dettaglio pagamento Satispay
    """
    id: str
    amount_unit: int  # in centesimi (es. 1000 = 10.00 EUR)
    currency: str = "EUR"
    status: str
    flow: Optional[str] = None  # es: "MATCH_CODE", "REFUND", ecc.
    type: Optional[str] = None  # es: "TO_BUSINESS", "TO_CONSUMER"
    sender_name: Optional[str] = None
    comment: Optional[str] = None
    insert_date: Optional[str] = None

    @property
    def amount_euro(self) -> float:
        return self.amount_unit / 100.0
