import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Set
import httpx
from config import settings
from core.models import SumUpTransaction

logger = logging.getLogger(__name__)

SUMUP_BASE_URL = "https://api.sumup.com"


def parse_sumup_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Converte un timestamp ISO SumUp (es. 2026-10-29T10:00:00.123Z) in datetime UTC"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SumUpService:
    """
    Servizio per l'integrazione con le API SumUp: storico transazioni, polling e checkout con link di pagamento
    """

    def __init__(self):
        self.base_url = SUMUP_BASE_URL
        self._merchant_code: Optional[str] = settings.SUMUP_MERCHANT_CODE or None

    @property
    def is_configured(self) -> bool:
        return bool(settings.SUMUP_API_KEY)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.SUMUP_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _get_merchant_code(self) -> Optional[str]:
        if self._merchant_code:
            return self._merchant_code
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{self.base_url}/v0.1/me", headers=self._headers(), timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                self._merchant_code = (data.get("merchant_profile") or {}).get("merchant_code")
                return self._merchant_code
            logger.error(f"Errore SumUp GET /me ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Eccezione durante recupero merchant code SumUp: {e}")
        return None

    @staticmethod
    def _parse_item(item: Dict[str, Any]) -> SumUpTransaction:
        card = item.get("card") if isinstance(item.get("card"), dict) else {}
        return SumUpTransaction(
            id=item.get("id"),
            transaction_code=item.get("transaction_code") or item.get("id") or "",
            amount=float(item.get("amount", 0) or 0),
            currency=item.get("currency", "EUR"),
            status=item.get("status", "UNKNOWN"),
            type=item.get("type"),
            payment_type=item.get("payment_type"),
            card_type=item.get("card_type") or card.get("type"),
            user=item.get("user"),
            timestamp=item.get("timestamp"),
        )

    async def get_transactions_history(self, limit: int = 50) -> List[SumUpTransaction]:
        """Recupera le ultime transazioni dello storico SumUp (dalla più recente)"""
        if not self.is_configured:
            return []
        merchant_code = await self._get_merchant_code()
        if not merchant_code:
            return []

        url = f"{self.base_url}/v2.1/merchants/{merchant_code}/transactions/history"
        params = {"limit": limit, "order": "desc"}
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=self._headers(), params=params, timeout=10.0)
            if res.status_code == 200:
                items = res.json().get("items") or []
                return [self._parse_item(i) for i in items if i.get("transaction_code") or i.get("id")]
            logger.error(f"Errore SumUp GET transactions history ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Eccezione durante recupero storico SumUp: {e}")
        return []

    async def get_transaction(self, transaction_code: str) -> Optional[SumUpTransaction]:
        """Recupera una singola transazione dato il transaction_code"""
        if not self.is_configured:
            return None
        merchant_code = await self._get_merchant_code()
        if not merchant_code:
            return None

        url = f"{self.base_url}/v2.1/merchants/{merchant_code}/transactions"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    url, headers=self._headers(), params={"transaction_code": transaction_code}, timeout=10.0
                )
            if res.status_code == 200:
                return self._parse_item(res.json())
            logger.error(f"Errore SumUp GET transaction ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Eccezione durante recupero transazione SumUp: {e}")
        return None

    async def poll_new_transactions(self) -> List[SumUpTransaction]:
        """
        Polling delle transazioni SumUp, stessa logica di Satispay:
        - primo avvio (DB senza transazioni SumUp): sincronizzazione silenziosa
        - poi restituisce (in ordine cronologico) le sole transazioni SUCCESSFUL non ancora note
        """
        from services.db_service import db_service

        transactions = await self.get_transactions_history(limit=50)
        if not transactions:
            return []

        if db_service.sumup_is_empty():
            logger.info(f"[SumUp Polling] Primo avvio rilevato. Sincronizzazione iniziale silenziosa di {len(transactions)} transazioni...")
            for t in transactions:
                db_service.save_sumup_transaction(t)
            return []

        new_transactions: List[SumUpTransaction] = []
        for t in sorted(transactions, key=lambda x: x.timestamp or ""):
            if not db_service.has_sumup_transaction(t.transaction_code):
                db_service.save_sumup_transaction(t)
                if t.status == "SUCCESSFUL":
                    new_transactions.append(t)
                    logger.info(f"[SumUp Polling] Rilevata nuova transazione: {t.transaction_code} ({t.amount:.2f}€)")
        return new_transactions

    async def find_new_payment(
        self,
        since_utc: datetime,
        exclude_codes: Optional[Set[str]] = None,
    ) -> Optional[SumUpTransaction]:
        """
        Cerca la prima transazione SUCCESSFUL di tipo PAYMENT avvenuta dopo `since_utc` (tolleranza 30s)
        e non già associata ad altre registrazioni. L'importo NON viene confrontato.
        """
        exclude_codes = exclude_codes or set()
        threshold = since_utc - timedelta(seconds=30)
        transactions = await self.get_transactions_history(limit=20)
        candidates = []
        for t in transactions:
            if t.status != "SUCCESSFUL" or (t.type and t.type != "PAYMENT"):
                continue
            if t.transaction_code in exclude_codes:
                continue
            ts = parse_sumup_timestamp(t.timestamp)
            if ts is None or ts < threshold:
                continue
            candidates.append((ts, t))
        if not candidates:
            return None
        return min(candidates, key=lambda c: c[0])[1]

    async def create_hosted_checkout(self, amount: float, description: str = "") -> Optional[Dict[str, Any]]:
        """Crea un checkout SumUp con pagina di pagamento ospitata. Restituisce id e hosted_checkout_url"""
        if not self.is_configured:
            return None
        merchant_code = await self._get_merchant_code()
        if not merchant_code:
            return None

        payload = {
            "checkout_reference": str(uuid.uuid4()),
            "amount": round(amount, 2),
            "currency": "EUR",
            "merchant_code": merchant_code,
            "description": description[:250],
            "hosted_checkout": {"enabled": True},
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"{self.base_url}/v0.1/checkouts", headers=self._headers(), json=payload, timeout=12.0
                )
            if res.status_code in (200, 201):
                data = res.json()
                if data.get("id") and data.get("hosted_checkout_url"):
                    return data
                logger.error(f"Risposta checkout SumUp priva di hosted_checkout_url: {data}")
            else:
                logger.error(f"Errore creazione checkout SumUp ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Eccezione durante creazione checkout SumUp: {e}")
        return None

    async def get_checkout(self, checkout_id: str) -> Optional[Dict[str, Any]]:
        """Recupera lo stato di un checkout (status e transactions[])"""
        if not self.is_configured:
            return None
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self.base_url}/v0.1/checkouts/{checkout_id}", headers=self._headers(), timeout=10.0
                )
            if res.status_code == 200:
                return res.json()
            logger.error(f"Errore SumUp GET checkout ({res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Eccezione durante recupero checkout SumUp: {e}")
        return None

    @staticmethod
    def checkout_result(checkout: Dict[str, Any]) -> Optional[tuple]:
        """
        Interpreta un checkout: ("PAID", transaction_code) se pagato, ("FAILED", stato) se fallito/scaduto,
        None se ancora in attesa.
        """
        status = checkout.get("status")
        if status == "PAID":
            txs = checkout.get("transactions") or []
            code = next(
                (t.get("transaction_code") for t in txs if t.get("status") in (None, "SUCCESSFUL") and t.get("transaction_code")),
                None,
            ) or checkout.get("transaction_code")
            return ("PAID", code)
        if status in ("FAILED", "EXPIRED"):
            return ("FAILED", status)
        return None


sumup_service = SumUpService()
