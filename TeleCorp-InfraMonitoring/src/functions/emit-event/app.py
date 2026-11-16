"""Construye el sobre `antenna.event` con event_id determinista y lo publica en las colas outbox."""
from common import emitter, log


def handler(event, context):
    log.set_correlation(event.get("episode_id"))
    return emitter.publish(event)
