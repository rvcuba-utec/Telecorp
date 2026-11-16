"""Colas: Service Bus en producción, memoria en pruebas."""
import json
import logging
import os

log = logging.getLogger("pulsored.queue")


class MemoryQueue:
    def __init__(self):
        self.messages = {}

    def send(self, queue, payloads):
        self.messages.setdefault(queue, []).extend(json.loads(json.dumps(p)) for p in payloads)

    def drain(self, queue):
        out, self.messages[queue] = self.messages.get(queue, []), []
        return out

    def redrive(self, queue, max_messages=100):
        return 0


class ServiceBusQueue:
    def __init__(self, conn=None):
        self.conn = conn or os.environ["SB"]

    def send(self, queue, payloads):
        from azure.servicebus import ServiceBusClient, ServiceBusMessage
        if not payloads:
            return
        with ServiceBusClient.from_connection_string(self.conn) as c, c.get_queue_sender(queue) as s:
            batch = s.create_message_batch()
            for p in payloads:
                msg = ServiceBusMessage(json.dumps(p), content_type="application/json")
                try:
                    batch.add_message(msg)
                except ValueError:  # lote lleno: enviar y empezar otro
                    s.send_messages(batch)
                    batch = s.create_message_batch()
                    batch.add_message(msg)
            if len(batch):
                s.send_messages(batch)

    def redrive(self, queue, max_messages=100):
        """Mueve mensajes de la dead-letter queue de vuelta a la cola principal."""
        from azure.servicebus import ServiceBusClient, ServiceBusMessage, ServiceBusSubQueue
        moved = 0
        with ServiceBusClient.from_connection_string(self.conn) as c:
            with c.get_queue_receiver(queue, sub_queue=ServiceBusSubQueue.DEAD_LETTER, max_wait_time=5) as rx, \
                    c.get_queue_sender(queue) as tx:
                for m in rx.receive_messages(max_message_count=max_messages, max_wait_time=5):
                    tx.send_messages(ServiceBusMessage(b"".join(m.body), content_type="application/json"))
                    rx.complete_message(m)
                    moved += 1
        return moved


_queue = None


def get_queue():
    global _queue
    if _queue is None:
        _queue = ServiceBusQueue() if os.environ.get("SB") else MemoryQueue()
    return _queue


def set_queue(q):
    global _queue
    _queue = q
