"""Local JSON API (stdlib ``http.server``), bound to 127.0.0.1 only.

GET  /api/games
GET  /api/runs
GET  /api/runs/current
GET  /api/career
GET  /api/controllers
GET  /api/doctor
GET  /api/runs/<id>
GET  /api/runs/<id>/events
POST /api/runs                  {"game", "ruleset", "profile", "seed", "launch"}
POST /api/runs/<id>/events      {"type", "payload"}
POST /api/runs/<id>/fail        {"reason"}
POST /api/runs/<id>/restart     {"reason"}
POST /api/runs/<id>/verify

Intended for local tools (a future tracker bridge, overlays). No auth: it
never listens on a public interface and never sends data anywhere.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .app import Application

_lock = threading.Lock()


def make_handler(app: Application):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NFNF-IronMON"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout quiet
            pass

        def _send(self, code: int, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body

        def _route(self, method: str) -> None:
            path = self.path.split("?")[0].rstrip("/")
            try:
                with _lock:
                    code, body = self._handle(method, path)
            except KeyError as exc:
                code, body = 404, {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                code, body = 400, {"error": str(exc)}
            self._send(code, body)

        def _handle(self, method: str, path: str) -> tuple[int, Any]:
            if method == "GET":
                if path == "/api/games":
                    return 200, app.list_games()
                if path == "/api/runs":
                    return 200, [app.run_summary(r) for r in app.list_runs()]
                if path == "/api/career":
                    return 200, app.career().to_dict()
                if path == "/api/controllers":
                    return 200, {"backends": app.controllers.backend_status(),
                                 "devices": [d.to_dict() for d in app.controllers.devices()],
                                 "mapping": app.controllers.mapping_id}
                if path == "/api/doctor":
                    return 200, app.doctor()
                if path == "/api/runs/current":
                    run = app.current_run()
                    return 200, app.run_summary(run) if run else None
                m = re.fullmatch(r"/api/runs/(RUN-\d+)(/events)?", path)
                if m:
                    if m.group(2):
                        return 200, app.runs.events(m.group(1))
                    run = app.runs.get(m.group(1))
                    return 200, {"summary": app.run_summary(run), "metadata": app.runs.metadata(run.id)}
            elif method == "POST":
                body = self._body()
                if path == "/api/runs":
                    run = app.new_run(game_id=body.get("game"), ruleset_id=body.get("ruleset"),
                                      profile_id=body.get("profile"), seed=body.get("seed"),
                                      launch=body.get("launch", True))
                    return 201, app.run_summary(run)
                m = re.fullmatch(r"/api/runs/(RUN-\d+)/(events|fail|restart|verify)", path)
                if m:
                    rid, action = m.groups()
                    if action == "events":
                        return 201, app.record_event(rid, body["type"], body.get("payload", {}),
                                                     source=body.get("source", "api"))
                    if action == "fail":
                        return 200, app.run_summary(app.fail_run(rid, body.get("reason", "player_failed")))
                    if action == "restart":
                        return 201, app.run_summary(app.restart_run(rid, body.get("reason", "player_failed")))
                    return 200, app.verify_run(rid).to_dict()
            raise KeyError(f"No route for {method} {path}")

        def do_GET(self) -> None:
            self._route("GET")

        def do_POST(self) -> None:
            self._route("POST")

    return Handler


def create_server(app: Application, port: int = 8765) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), make_handler(app))


def serve(app: Application, port: int = 8765) -> None:
    server = create_server(app, port)
    print(f"NFNF IronMON API on http://127.0.0.1:{server.server_address[1]}/api/runs (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
