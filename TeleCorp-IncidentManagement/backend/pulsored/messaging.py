"""Canales de salida al usuario. Email real con Azure Communication Services si está configurado;
SMS siempre simulado (ACS SMS exige comprar un número, no disponible en cuentas de estudiante)."""
import logging
import os

log = logging.getLogger("pulsored.messaging")


def _mask(addr: str) -> str:
    return (addr[:2] + "***" + addr[addr.find("@"):]) if "@" in addr else addr[:3] + "***"


class SimulatedSender:
    def __init__(self):
        self.sent = []

    def email(self, to, subject, text):
        self.sent.append(("EMAIL_SIMULATED", to, subject, text))
        log.info("[email simulado] a=%s asunto=%s", _mask(to), subject)
        return "EMAIL_SIMULATED"

    def sms(self, to, text):
        self.sent.append(("SMS_SIMULATED", to, "", text))
        log.info("[sms simulado] a=%s", _mask(to))
        return "SMS_SIMULATED"


class AcsSender(SimulatedSender):
    def __init__(self):
        super().__init__()
        from azure.communication.email import EmailClient
        self.client = EmailClient.from_connection_string(os.environ["ACS_CONNECTION_STRING"])
        self.sender = os.environ["ACS_SENDER"]

    def email(self, to, subject, text):
        poller = self.client.begin_send({
            "senderAddress": self.sender,
            "recipients": {"to": [{"address": to}]},
            "content": {"subject": subject, "plainText": text},
        })
        poller.result()
        return "EMAIL"


_sender = None


def get_sender():
    global _sender
    if _sender is None:
        ok = os.environ.get("ACS_CONNECTION_STRING") and os.environ.get("ACS_SENDER")
        _sender = AcsSender() if ok else SimulatedSender()
    return _sender


def set_sender(s):
    global _sender
    _sender = s
