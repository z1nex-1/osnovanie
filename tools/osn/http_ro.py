from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cost, review, workflow
from .store import OsnError, active_project

OPENAPI = {
    "openapi": "3.1.0", "info": {"title": "Основание: чтение состояния", "version": "1.0.0"},
    "paths": {p: {"get": {"responses": {"200": {"description": "JSON"}}}} for p in ("/health", "/v1/status", "/v1/claims", "/v1/claims/{id}", "/v1/options", "/v1/cost", "/v1/gates", "/v1/next", "/v1/map")},
}


def _route(path: str):
    from .cli import status_data

    project = active_project()
    if path == "/v1/status":
        return status_data(project)
    if path == "/v1/claims":
        return list(project.current("claims").values())
    if path.startswith("/v1/claims/"):
        claims = project.current("claims")
        return claims.get(path.rsplit("/", 1)[1])
    if path == "/v1/options":
        return list(project.current("options").values())
    if path == "/v1/cost":
        return cost.summary(project)
    if path == "/v1/gates":
        return workflow.load(project)["gates"]
    if path == "/v1/next":
        return review.what_next(project)
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, kind: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            return self._json(200, {"status": "ok"})
        if path == "/v1/openapi.json":
            return self._json(200, OPENAPI)
        token = os.environ.get("OSNOVANIE_API_TOKEN")
        given = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if token and not hmac.compare_digest(token, given):
            return self._json(401, {"error": "Нужен токен"})
        try:
            if path == "/v1/map":
                target = active_project().outputs / "trust-map.html"
                if not target.exists():
                    return self._json(404, {"error": "Карта ещё не собрана"})
                return self._send(200, target.read_bytes(), "text/html; charset=utf-8")
            payload = _route(path)
        except OsnError as error:
            return self._json(400, {"error": str(error)})
        if payload is None:
            return self._json(404, {"error": "Не найдено"})
        return self._json(200, payload)

    def do_POST(self):  # noqa: N802
        self._json(405, {"error": "Сервис только для чтения"})

    do_PUT = do_DELETE = do_PATCH = do_POST  # noqa: N815

    def log_message(self, *args):
        pass


def serve(port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}/v1/status — только чтение, только локальный адрес")
    server.serve_forever()
