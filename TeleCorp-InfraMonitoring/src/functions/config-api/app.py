"""GET /config — configuración global versionada, sin secretos (RF-AWS-24)."""
from common import auth, config


def handler(event, context):
    denied = auth.check(event)
    if denied:
        return denied
    cfg = config.get_config()
    # `items` es el contrato; las mismas claves van también en el nivel superior (así las lee Azure).
    return auth.response(200, {**cfg, "config_version": config.config_version(cfg), "zones_version": cfg["ZONES_VERSION"],
                               "items": cfg})
