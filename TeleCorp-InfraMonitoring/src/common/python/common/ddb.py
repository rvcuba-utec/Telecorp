"""DynamoDB con el cliente de bajo nivel (seguro entre hilos) y conversión de tipos."""
from decimal import Decimal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from . import aws

_ser = TypeSerializer()
_des = TypeDeserializer()


def clean(value):
    """float -> Decimal, recursivo; descarta None dentro de diccionarios."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def to_item(d: dict) -> dict:
    return {k: _ser.serialize(clean(v)) for k, v in d.items() if v is not None}


def to_value(v):
    return _ser.serialize(clean(v))


def from_item(item: dict) -> dict:
    return {k: _des.deserialize(v) for k, v in item.items()}


def native(value):
    """Decimal -> int/float para poder serializar a JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [native(v) for v in value]
    return value


def db():
    return aws.client("dynamodb")


def get(table: str, key: dict, consistent=False):
    r = db().get_item(TableName=table, Key=to_item(key), ConsistentRead=consistent)
    return from_item(r["Item"]) if "Item" in r else None


def put(table: str, item: dict, condition: str = None, values: dict = None, names: dict = None):
    kw = {"TableName": table, "Item": to_item(item)}
    if condition:
        kw["ConditionExpression"] = condition
    if values:
        kw["ExpressionAttributeValues"] = to_item(values)
    if names:
        kw["ExpressionAttributeNames"] = names
    db().put_item(**kw)


def update_kwargs(table: str, key: dict, sets: dict = None, removes=(), condition: str = None,
                  cond_values: dict = None, names: dict = None) -> dict:
    """Argumentos de UpdateItem (sirven también para un Update dentro de TransactWriteItems)."""
    expr, values, nm = [], dict(cond_values or {}), dict(names or {})
    removes = list(removes)
    parts = []
    for i, (k, v) in enumerate((sets or {}).items()):
        if v is None:
            removes.append(k)
            continue
        parts.append(f"#s{i} = :s{i}")
        nm[f"#s{i}"] = k
        values[f":s{i}"] = v
    if parts:
        expr.append("SET " + ", ".join(parts))
    rem = []
    for j, k in enumerate(removes):
        nm[f"#r{j}"] = k
        rem.append(f"#r{j}")
    if rem:
        expr.append("REMOVE " + ", ".join(rem))
    kw = {"TableName": table, "Key": to_item(key), "UpdateExpression": " ".join(expr)}
    if values:
        kw["ExpressionAttributeValues"] = to_item(values)
    if nm:
        kw["ExpressionAttributeNames"] = nm
    if condition:
        kw["ConditionExpression"] = condition
    return kw


def update(table: str, key: dict, sets: dict = None, removes=(), condition: str = None,
           cond_values: dict = None, names: dict = None, return_values: str = "NONE"):
    kw = update_kwargs(table, key, sets, removes, condition, cond_values, names)
    r = db().update_item(ReturnValues=return_values, **kw)
    return from_item(r["Attributes"]) if r.get("Attributes") else None


def transact(actions: list):
    """TransactWriteItems. `actions` = [{"Update": kwargs} | {"Put": {...}}]."""
    db().transact_write_items(TransactItems=actions)


def put_action(table: str, item: dict) -> dict:
    return {"Put": {"TableName": table, "Item": to_item(item)}}


def is_conflict(exc) -> bool:
    """Conflicto de bloqueo optimista o de transacciones concurrentes."""
    code = error_code(exc)
    if code in ("ConditionalCheckFailedException", "TransactionConflictException"):
        return True
    if code == "TransactionCanceledException":
        reasons = getattr(exc, "response", {}).get("CancellationReasons", []) or []
        return any(r.get("Code") in ("ConditionalCheckFailed", "TransactionConflict") for r in reasons) or not reasons
    return False


def batch_get(table: str, keys: list, key_name: str = "antenna_id", consistent: bool = False) -> dict:
    """BatchGetItem con reintento de claves no procesadas. Devuelve {valor de la clave: item}."""
    out, pending = {}, [to_item(k) for k in keys]
    for i in range(0, len(pending), 100):
        req = {table: {"Keys": pending[i:i + 100], "ConsistentRead": consistent}}
        for _ in range(6):
            r = db().batch_get_item(RequestItems=req)
            for it in r["Responses"].get(table, []):
                d = from_item(it)
                out[d[key_name]] = d
            req = r.get("UnprocessedKeys") or {}
            if not req:
                break
    return out


def batch_write(table: str, items: list):
    """BatchWriteItem (puts) con reintento de pendientes."""
    for i in range(0, len(items), 25):
        req = {table: [{"PutRequest": {"Item": to_item(it)}} for it in items[i:i + 25]]}
        for attempt in range(8):
            r = db().batch_write_item(RequestItems=req)
            req = r.get("UnprocessedItems") or {}
            if not req:
                break
        else:
            raise RuntimeError("BatchWriteItem: quedaron elementos sin procesar")


def query(table: str, key_cond: str, values: dict, names: dict = None, index: str = None,
          filter_expr: str = None, forward=True, limit: int = None, projection: str = None):
    kw = {"TableName": table, "KeyConditionExpression": key_cond,
          "ExpressionAttributeValues": to_item(values), "ScanIndexForward": forward}
    if names:
        kw["ExpressionAttributeNames"] = names
    if index:
        kw["IndexName"] = index
    if filter_expr:
        kw["FilterExpression"] = filter_expr
    if projection:
        kw["ProjectionExpression"] = projection
    items = []
    while True:
        r = db().query(**kw)
        items += [from_item(i) for i in r.get("Items", [])]
        if limit and len(items) >= limit:
            return items[:limit]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def scan(table: str, projection: str = None, names: dict = None, filter_expr: str = None, values: dict = None):
    kw = {"TableName": table}
    if projection:
        kw["ProjectionExpression"] = projection
    if names:
        kw["ExpressionAttributeNames"] = names
    if filter_expr:
        kw["FilterExpression"] = filter_expr
    if values:
        kw["ExpressionAttributeValues"] = to_item(values)
    items = []
    while True:
        r = db().scan(**kw)
        items += [from_item(i) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def error_code(exc) -> str:
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")


def is_throttle(exc) -> bool:
    return error_code(exc) in ("ProvisionedThroughputExceededException", "ThrottlingException",
                               "RequestLimitExceeded")
