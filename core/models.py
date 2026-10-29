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
    method: str  # "Contanti", "Satispay", "SumUp" oppure "Trasferimento"
    box_red: Optional[float] = None  # Saldo Cassa Rossa prima della transazione
    box_blue: Optional[float] = None  # Saldo Cassa Blu prima della transazione
    description: str
    flow: str  # "Entrata", "Uscita" oppure "Trasferimento"
    amount: float
    box_red_updated: Optional[float] = None  # Saldo Cassa Rossa dopo la transazione
    box_blue_updated: Optional[float] = None  # Saldo Cassa Blu dopo la transazione
    receipt_number: int  # Numero progressivo ricevuta (0 se non applicabile)
    telegram_username: str
    telegram_user_id: int
    satispay_id: Optional[str] = None
    sumup_id: Optional[str] = None
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
            f"{self.box_red:.2f}" if self.box_red is not None else "",
            f"{self.box_blue:.2f}" if self.box_blue is not None else "",
            self.description,
            self.flow,
            f"{self.amount:.2f}",
            f"{self.box_red_updated:.2f}" if self.box_red_updated is not None else "",
            f"{self.box_blue_updated:.2f}" if self.box_blue_updated is not None else "",
            self.receipt_number,
            self.telegram_username,
            str(self.telegram_user_id),
            self.satispay_id or "",
            format_datetime_for_sheet(self.created_at),
            self.sumup_id or ""
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
            "Cassa Rossa Iniziale (€)",
            "Cassa Blu Iniziale (€)",
            "Descrizione",
            "Flusso",
            "Importo (€)",
            "Cassa Rossa Aggiornata (€)",
            "Cassa Blu Aggiornata (€)",
            "Numero Ricevuta",
            "Operatore",
            "Telegram ID",
            "ID Satispay",
            "Data Registrazione",
            "ID SumUp"
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
    box_red: Optional[float] = None
    box_blue: Optional[float] = None


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


class SumUpTransaction(BaseModel):
    """
    Modello per una transazione SumUp (storico transazioni del merchant)
    """
    id: Optional[str] = None
    transaction_code: str
    amount: float  # in euro
    currency: str = "EUR"
    status: str  # es: "SUCCESSFUL", "FAILED", "PENDING"
    type: Optional[str] = None  # es: "PAYMENT", "REFUND"
    payment_type: Optional[str] = None  # es: "POS", "ECOM"
    card_type: Optional[str] = None
    user: Optional[str] = None
    timestamp: Optional[str] = None

    @property
    def amount_euro(self) -> float:
        return self.amount
