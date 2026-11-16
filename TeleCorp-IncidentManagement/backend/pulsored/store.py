"""Capa de datos. Dos implementaciones con la misma interfaz:
 - CosmosStore  : producción (Cosmos DB NoSQL)
 - MemoryStore  : pruebas y simulación local (sin Azure)
"""
import copy
import itertools
import logging
import os
import threading
import time
import uuid

from . import clock, config, domain

log = logging.getLogger("pulsored.store")

PK = {
    "reports": "zone_id",
    "incidents": "zone_id",
    "incident_users": "incident_id",
    "history": "entity_id",
    "infra_events": "zone_id",
    "notifications": "incident_id",
    "zones": "id",
}
_seq = itertools.count(1)
_SYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}

ZONES = domain.ZONES   # catálogo único (contracts/zones.json)


def clean(d):
    """Quita los campos de sistema de Cosmos. Las consultas `SELECT VALUE COUNT(1)` devuelven escalares: se dejan igual."""
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if k not in _SYS}


class BaseStore:
    # ---- comunes ----
    def get_zone(self, zone_id):
        return ZONES.get(zone_id)

    def list_zones(self):
        return list(ZONES.values())

    def filter_incidents(self, items, incident_type=None, priority_level=None, status=None):
        out = [i for i in items
               if (not incident_type or i.get("incident_type", "COLECTIVA") == incident_type)
               and (not priority_level or i.get("priority_level") == priority_level)
               and (not status or i.get("status") == status)]
        return sorted(out, key=lambda i: -(i.get("priority_score") or 0))

    def add_history(self, entity_id, frm, to, actor, action, note=""):
        """Historial append-only (RF-AZ-05/15)."""
        ts = clock.iso()
        seq = next(_seq)  # contador del proceso: orden estable aunque dos entradas compartan timestamp
        doc = {
            "id": f"{ts}|{seq:06d}{uuid.uuid4().hex[:6]}", "entity_id": entity_id, "ts": ts,
            "from": frm, "to": to, "actor": actor, "action": action, "note": note,
        }
        self.create("history", doc)
        return doc


class MemoryStore(BaseStore):
    def __init__(self):
        self._lock = threading.RLock()
        self.data = {c: {} for c in PK}

    def _k(self, c, doc_or_id, pk=None):
        return (pk if pk is not None else doc_or_id[PK[c]], doc_or_id if isinstance(doc_or_id, str) else doc_or_id["id"])

    def create(self, c, doc):
        with self._lock:
            k = self._k(c, doc)
            if k in self.data[c]:
                return False
            self.data[c][k] = copy.deepcopy(doc)
            return True

    def get(self, c, id, pk):
        with self._lock:
            d = self.data[c].get((pk, id))
            return copy.deepcopy(d) if d else None

    def upsert(self, c, doc):
        with self._lock:
            self.data[c][self._k(c, doc)] = copy.deepcopy(doc)

    def _all(self, c):
        return [copy.deepcopy(d) for d in self.data[c].values()]

    # ---- consultas específicas ----
    def reports_unlinked_since(self, zone, group, since):
        return [r for r in self._all("reports")
                if r["zone_id"] == zone and r.get("corr_group") == group and r["created_at"] >= since
                and not r.get("incident_id") and r["status"] != "CERRADA"]

    def count_reports_by_user_since(self, user_key, since):
        return len([r for r in self._all("reports") if r["user_key"] == user_key and r["created_at"] >= since])

    def reports_for_incident(self, incident_id, zone):
        return [r for r in self._all("reports") if r["zone_id"] == zone and r.get("incident_id") == incident_id]

    def find_open_incident(self, zone, group):
        for i in self._all("incidents"):
            if i["zone_id"] == zone and i.get("corr_group") == group and i["status"] != "CERRADA" \
                    and i.get("incident_type", "COLECTIVA") == "COLECTIVA":
                return i
        return None

    def find_open_typed_incident(self, zone, incident_type, antenna_id):
        for i in self._all("incidents"):
            if i["zone_id"] == zone and i.get("incident_type") == incident_type and i["status"] != "CERRADA" \
                    and antenna_id in (i.get("antenna_ids") or []):
                return i
        return None

    def open_incidents_in_zone(self, zone):
        return [i for i in self._all("incidents") if i["zone_id"] == zone and i["status"] != "CERRADA"
                and i.get("incident_type", "COLECTIVA") == "COLECTIVA"]

    def list_incidents(self, zone=None, include_closed=False, **filters):
        out = [i for i in self._all("incidents")
               if (zone is None or i["zone_id"] == zone) and (include_closed or i["status"] != "CERRADA")]
        return self.filter_incidents(out, **filters)

    def count_incidents_since(self, zone, since, exclude_id=None):
        return len([i for i in self._all("incidents")
                    if i["zone_id"] == zone and i["opened_at"] >= since and i["id"] != exclude_id])

    def add_incident_user(self, incident_id, user_key):
        return self.create("incident_users", {"id": user_key, "incident_id": incident_id})

    def count_incident_users(self, incident_id):
        return len([1 for (pk, _id) in self.data["incident_users"] if pk == incident_id])

    def list_history(self, entity_id):
        return sorted([h for h in self._all("history") if h["entity_id"] == entity_id], key=lambda h: h["id"])

    def infra_events(self, zone, since):
        return [e for e in self._all("infra_events") if e["zone_id"] == zone and e["occurred_at"] >= since]

    def list_infra_events(self, zone=None, limit=100):
        out = [e for e in self._all("infra_events") if zone is None or e["zone_id"] == zone]
        return sorted(out, key=lambda e: e["occurred_at"], reverse=True)[:limit]


class CosmosStore(BaseStore):
    def __init__(self, connection_string=None):
        from azure.cosmos import CosmosClient
        self.client = CosmosClient.from_connection_string(connection_string or os.environ["COSMOS"])
        self.db = self.client.get_database_client(config.cosmos_db())
        self._c = {}

    def _cont(self, name):
        if name not in self._c:
            self._c[name] = self.db.get_container_client(name)
        return self._c[name]

    def _q(self, c, sql, params=None, pk=None):
        kw = {"partition_key": pk} if pk is not None else {"enable_cross_partition_query": True}
        return [clean(d) for d in self._cont(c).query_items(sql, parameters=params or [], **kw)]

    @staticmethod
    def _retry(fn, attempts=4):
        """Reintento propio ante 429/503 de Cosmos (además del que hace el SDK). RF-AZ-27."""
        from azure.cosmos import exceptions
        for i in range(attempts):
            try:
                return fn()
            except exceptions.CosmosResourceExistsError:
                raise
            except exceptions.CosmosHttpResponseError as e:
                if e.status_code not in (429, 503) or i == attempts - 1:
                    raise
                log.warning("Cosmos %s; reintento %d", e.status_code, i + 1)
                time.sleep(min(4.0, 0.5 * 2 ** i))

    def create(self, c, doc):
        from azure.cosmos import exceptions
        try:
            self._retry(lambda: self._cont(c).create_item(doc))
            return True
        except exceptions.CosmosResourceExistsError:
            return False

    def get(self, c, id, pk):
        from azure.cosmos import exceptions
        try:
            return clean(self._cont(c).read_item(id, partition_key=pk))
        except exceptions.CosmosResourceNotFoundError:
            return None

    def upsert(self, c, doc):
        self._retry(lambda: self._cont(c).upsert_item(doc))

    def count_reports_by_user_since(self, user_key, since):
        r = self._q("reports", "SELECT VALUE COUNT(1) FROM c WHERE c.user_key=@u AND c.created_at>=@t",
                    [{"name": "@u", "value": user_key}, {"name": "@t", "value": since}])
        return sum(r) if r else 0

    def reports_unlinked_since(self, zone, group, since):
        return self._q(
            "reports",
            "SELECT * FROM c WHERE c.zone_id=@z AND c.corr_group=@g AND c.created_at>=@t "
            "AND (NOT IS_DEFINED(c.incident_id) OR IS_NULL(c.incident_id)) AND c.status != 'CERRADA'",
            [{"name": "@z", "value": zone}, {"name": "@g", "value": group}, {"name": "@t", "value": since}], pk=zone)

    def reports_for_incident(self, incident_id, zone):
        return self._q("reports", "SELECT * FROM c WHERE c.zone_id=@z AND c.incident_id=@i",
                       [{"name": "@z", "value": zone}, {"name": "@i", "value": incident_id}], pk=zone)

    _COLECTIVA = "(NOT IS_DEFINED(c.incident_type) OR c.incident_type = 'COLECTIVA')"

    def find_open_incident(self, zone, group):
        r = self._q("incidents", "SELECT * FROM c WHERE c.zone_id=@z AND c.corr_group=@g AND c.status != 'CERRADA' AND "
                    + self._COLECTIVA, [{"name": "@z", "value": zone}, {"name": "@g", "value": group}], pk=zone)
        return r[0] if r else None

    def find_open_typed_incident(self, zone, incident_type, antenna_id):
        r = self._q("incidents", "SELECT * FROM c WHERE c.zone_id=@z AND c.incident_type=@t AND c.status != 'CERRADA' "
                    "AND ARRAY_CONTAINS(c.antenna_ids, @a)",
                    [{"name": "@z", "value": zone}, {"name": "@t", "value": incident_type},
                     {"name": "@a", "value": antenna_id}], pk=zone)
        return r[0] if r else None

    def open_incidents_in_zone(self, zone):
        return self._q("incidents", "SELECT * FROM c WHERE c.zone_id=@z AND c.status != 'CERRADA' AND " + self._COLECTIVA,
                       [{"name": "@z", "value": zone}], pk=zone)

    def list_incidents(self, zone=None, include_closed=False, **filters):
        conds, params = [], []
        if zone:
            conds.append("c.zone_id=@z")
            params.append({"name": "@z", "value": zone})
        if not include_closed:
            conds.append("c.status != 'CERRADA'")
        sql = "SELECT * FROM c" + (" WHERE " + " AND ".join(conds) if conds else "")
        out = self._q("incidents", sql, params, pk=zone if zone else None)
        return self.filter_incidents(out, **filters)

    def count_incidents_since(self, zone, since, exclude_id=None):
        r = self._q("incidents", "SELECT VALUE COUNT(1) FROM c WHERE c.zone_id=@z AND c.opened_at>=@t AND c.id != @x",
                    [{"name": "@z", "value": zone}, {"name": "@t", "value": since}, {"name": "@x", "value": exclude_id or ""}],
                    pk=zone)
        return r[0] if r else 0

    def add_incident_user(self, incident_id, user_key):
        return self.create("incident_users", {"id": user_key, "incident_id": incident_id})

    def count_incident_users(self, incident_id):
        r = self._q("incident_users", "SELECT VALUE COUNT(1) FROM c", pk=incident_id)
        return r[0] if r else 0

    def list_history(self, entity_id):
        return self._q("history", "SELECT * FROM c WHERE c.entity_id=@e ORDER BY c.ts",
                       [{"name": "@e", "value": entity_id}], pk=entity_id)

    def infra_events(self, zone, since):
        return self._q("infra_events", "SELECT * FROM c WHERE c.zone_id=@z AND c.occurred_at>=@t",
                       [{"name": "@z", "value": zone}, {"name": "@t", "value": since}], pk=zone)

    def list_infra_events(self, zone=None, limit=100):
        if zone:
            out = self._q("infra_events", "SELECT * FROM c WHERE c.zone_id=@z", [{"name": "@z", "value": zone}], pk=zone)
        else:
            out = self._q("infra_events", "SELECT * FROM c")
        return sorted(out, key=lambda e: e["occurred_at"], reverse=True)[:limit]


_store = None


def get_store():
    """Cosmos si existe la variable COSMOS; si no, memoria (desarrollo/simulación local)."""
    global _store
    if _store is None:
        _store = CosmosStore() if os.environ.get("COSMOS") else MemoryStore()
    return _store


def set_store(s):
    global _store
    _store = s
