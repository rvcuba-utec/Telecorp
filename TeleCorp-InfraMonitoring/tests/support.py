"""Utilidades de prueba compartidas."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_function(name: str):
    """Importa src/functions/<name>/app.py con un nombre de módulo único (todas se llaman `app`)."""
    path = ROOT / "src" / "functions" / name / "app.py"
    spec = importlib.util.spec_from_file_location("fn_" + name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def api_event(route, caller="operator", body=None, query=None, path=None, raw_path=None):
    """Evento de API Gateway HTTP API (formato 2.0) con el resultado del authorizer."""
    method = route.split()[0]
    return {"routeKey": route, "rawPath": raw_path or route.split()[1], "pathParameters": path or {},
            "queryStringParameters": query, "headers": {}, "body": json.dumps(body) if body is not None else None,
            "requestContext": {"authorizer": {"lambda": {"caller": caller}}, "http": {"method": method}}}
