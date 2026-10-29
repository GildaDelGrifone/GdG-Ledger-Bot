import base64
import email.utils
import hashlib
import json
import logging
from typing import Optional, Dict, Any, List
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from config import settings
from core.models import SatispayPayment
from core.qr import generate_qr_code_image

logger = logging.getLogger(__name__)


class SatispayService:
    """
    Servizio per l'integrazione con Satispay Business API (firma richieste HTTP e parsing notifiche)
    """

    def __init__(self):
        self.is_staging = settings.SATISPAY_STAGING
        self.base_url = (
            "https://staging.authservices.satispay.com"
            if self.is_staging
            else "https://authservices.satispay.com"
        )
        self.key_id = settings.SATISPAY_KEY_ID
        self.private_key = self._load_private_key()

    def _load_private_key(self):
        path = settings.satispay_private_key_path
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None)
        except Exception as e:
            logger.warning(f"Impossibile caricare la chiave privata Satispay da {path}: {e}")
            return None

    def generate_auth_header(self, method: str, path: str, body: bytes = b"") -> Dict[str, str]:
        """
        Genera l'intestazione Authorization conforme allo standard HTTP Signatures di Satispay
        """
        if not self.private_key or not self.key_id:
            raise ValueError("Satispay non configurato: mancano KeyID o chiave privata RSA.")

        date_header = email.utils.formatdate(usegmt=True)
        host_header = self.base_url.replace("https://", "").replace("http://", "").split("/")[0]

        digest_sha256 = hashlib.sha256(body).digest()
        digest_b64 = base64.b64encode(digest_sha256).decode("utf-8")
        digest_header = f"SHA-256={digest_b64}"

        signature_string = (
            f"(request-target): {method.lower()} {path}\n"
            f"host: {host_header}\n"
            f"date: {date_header}\n"
            f"digest: {digest_header}"
        )

        signed_bytes = self.private_key.sign(
            signature_string.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        signature_b64 = base64.b64encode(signed_bytes).decode("utf-8")

        auth_header_value = (
            f'Signature keyId="{self.key_id}", algorithm="rsa-sha256", '
            f'headers="(request-target) host date digest", signature="{signature_b64}"'
        )

        return {
            "Host": host_header,
            "Date": date_header,
            "Digest": digest_header,
            "Authorization": auth_header_value
        }

    async def get_payment(self, payment_id: str) -> Optional[SatispayPayment]:
        """
        Recupera i dettagli di un pagamento tramite API Satispay
        """
        path = f"/g_business/v1/payments/{payment_id}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        if self.private_key and self.key_id:
            auth_headers = self.generate_auth_header("GET", path)
            headers.update(auth_headers)

        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    return SatispayPayment(
                        id=data.get("id", payment_id),
                        amount_unit=data.get("amount_unit", 0),
                        currency=data.get("currency", "EUR"),
                        status=data.get("status", "UNKNOWN"),
                        flow=data.get("flow"),
                        type=data.get("type"),
                        sender_name=(
                            (data.get("sender", {}).get("name") if isinstance(data.get("sender"), dict) else None)
                            or data.get("sender_value")
                            or data.get("consumer_name")
                        ),
                        comment=data.get("comment"),
                        insert_date=data.get("insert_date")
                    )
                else:
                    logger.error(f"Errore chiamata Satispay GET payment ({res.status_code}): {res.text}")
                    return None
        except Exception as e:
            logger.error(f"Eccezione durante chiamata Satispay: {e}")
            return None

    async def create_payment(self, amount_unit: int, description: str = "") -> Optional[Dict[str, Any]]:
        """
        Crea una richiesta di pagamento su Satispay (POST /g_business/v1/payments).
        Restituisce un dizionario contenente id, redirect_url, status ('PENDING'), ecc.
        """
        path = "/g_business/v1/payments"
        payload = {
            "flow": "MATCH_CODE",
            "amount_unit": amount_unit,
            "currency": "EUR"
        }
        if description:
            payload["comment"] = description[:250]

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        if self.private_key and self.key_id:
            auth_headers = self.generate_auth_header("POST", path, body)
            headers.update(auth_headers)
        else:
            logger.error("Impossibile creare pagamento Satispay: credenziali assenti.")
            return None

        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, headers=headers, content=body, timeout=12.0)
                if res.status_code in (200, 201):
                    return res.json()
                else:
                    logger.error(f"Errore creazione pagamento Satispay ({res.status_code}): {res.text}")
                    return None
        except Exception as e:
            logger.error(f"Eccezione durante creazione pagamento Satispay: {e}")
            return None

    @staticmethod
    def generate_qr_code_image(data_to_encode: str):
        """
        Genera un'immagine PNG in memoria contenente il codice QR della stringa o URL passato
        """
        return generate_qr_code_image(data_to_encode)

    async def get_payments_history(self, limit: int = 50) -> List[SatispayPayment]:
        """
        Recupera la lista degli ultimi pagamenti dalle API di Satispay
        """
        path = f"/g_business/v1/payments?limit={limit}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        if self.private_key and self.key_id:
            auth_headers = self.generate_auth_header("GET", path)
            headers.update(auth_headers)
        else:
            return []

        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    items = (
                        data.get("data")
                        or data.get("list")
                        or (data if isinstance(data, list) else [])
                    )
                    payments: List[SatispayPayment] = []
                    for item in items:
                        payments.append(
                            SatispayPayment(
                                id=item.get("id"),
                                amount_unit=item.get("amount_unit", 0),
                                currency=item.get("currency", "EUR"),
                                status=item.get("status", "UNKNOWN"),
                                flow=item.get("flow"),
                                type=item.get("type"),
                                sender_name=(
                                    (item.get("sender", {}).get("name") if isinstance(item.get("sender"), dict) else None)
                                    or item.get("sender_value")
                                    or item.get("consumer_name")
                                ),
                                comment=item.get("comment"),
                                insert_date=item.get("insert_date")
                            )
                        )
                    return payments
                else:
                    logger.error(f"Errore chiamata Satispay GET payments ({res.status_code}): {res.text}")
                    return []
        except Exception as e:
            logger.error(f"Eccezione durante recupero storico Satispay: {e}")
            return []

    async def poll_new_payments(self) -> List[SatispayPayment]:
        """
        Esegue il polling dei pagamenti:
        - Al primo avvio (se DB locale vuoto), sincronizza silenziosamente lo storico senza inviare notifiche.
        - Negli avvii successivi, qualsiasi pagamento non presente nel DB locale viene considerato nuovo,
          salvato in SQLite e restituito per l'invio della notifica su Telegram.
        """
        from services.db_service import db_service

        payments = await self.get_payments_history(limit=50)
        if not payments:
            return []

        # Se il database è completamente vuoto, popoliamo lo storico silenziosamente
        if db_service.is_empty():
            logger.info(f"[Satispay Polling] Primo avvio rilevato. Sincronizzazione iniziale silenziosa di {len(payments)} pagamenti...")
            for p in payments:
                db_service.save_payment(p)
            return []

        new_payments: List[SatispayPayment] = []
        # Ordiniamo dal più vecchio al più recente per inviare le notifiche in ordine cronologico
        for p in reversed(payments):
            if not db_service.has_payment(p.id):
                db_service.save_payment(p)
                new_payments.append(p)
                logger.info(f"[Satispay Polling] Rilevato nuovo pagamento ID: {p.id} ({p.amount_euro:.2f}€ da {p.sender_name})")

        return new_payments

    def parse_webhook_payload(self, raw_data: Dict[str, Any]) -> SatispayPayment:
        """
        Converte i dati grezzi ricevuti dal webhook in un oggetto SatispayPayment
        """
        payment_id = raw_data.get("id") or raw_data.get("payment_id") or "ID_SCONOSCIUTO"
        amount_unit = raw_data.get("amount_unit")
        if amount_unit is None and "amount" in raw_data:
            amount_unit = int(float(raw_data["amount"]) * 100)
        elif amount_unit is None:
            amount_unit = 0

        sender = (
            raw_data.get("sender_name")
            or raw_data.get("sender_value")
            or raw_data.get("consumer_name")
            or (raw_data.get("sender", {}).get("name") if isinstance(raw_data.get("sender"), dict) else None)
        )

        return SatispayPayment(
            id=payment_id,
            amount_unit=int(amount_unit),
            currency=raw_data.get("currency", "EUR"),
            status=raw_data.get("status", "ACCEPTED"),
            flow=raw_data.get("flow", "MATCH_CODE"),
            type=raw_data.get("type", "TO_BUSINESS"),
            sender_name=sender or "Utente Satispay",
            comment=raw_data.get("comment"),
            insert_date=raw_data.get("insert_date")
        )


satispay_service = SatispayService()
