"""Loopback-only dashboard with explicit assets, JSON endpoints and CSRF protection."""

import json
import logging
import secrets
import sqlite3
import threading
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from jobhunter.evaluation.tier import TIERS, label
from jobhunter.workspace import Archive, ROOT, categories

logger = logging.getLogger(__name__)


class LocalHTTPServer(ThreadingHTTPServer):
    """Own the loopback address exclusively, including on Windows."""

    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        """Prevent a second dashboard from silently sharing the listening port."""
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        """Release the cache's watch connection too, or Windows keeps the database file locked."""
        super().server_close()
        if getattr(self, 'read_cache', None):
            self.read_cache.close()


class ReadCache:
    """Riusa le letture aggregate più care (Pipeline, Metriche) finché archivio e regole non cambiano.

    `PRAGMA data_version` su una connessione tenuta aperta cambia a ogni commit di qualunque altra
    connessione: richieste della dashboard, passaggi della pipeline, CLI. La versione si legge
    *prima* del calcolo, così una scrittura avvenuta durante il calcolo fa ricalcolare la volta dopo.
    Le regole stanno in file, non nel database: contano anche le date di modifica di quei file.
    """

    def __init__(self, database, root=ROOT):
        self.watch = sqlite3.connect(database, check_same_thread=False)
        self.lock = threading.Lock()
        self.folders = [root / "config", root / "user_context"]
        self.saved = {}

    def version(self):
        """Stato corrente di archivio e file di configurazione, senza leggere i dati."""
        with self.lock:
            data = self.watch.execute("PRAGMA data_version").fetchone()[0]
        files = tuple((str(p), p.stat().st_mtime_ns) for folder in self.folders if folder.exists()
                      for p in sorted(folder.rglob("*")) if p.is_file())
        return data, files

    def get(self, key, compute):
        """Restituisce il risultato salvato se nulla è cambiato, altrimenti lo ricalcola."""
        version = self.version()
        hit = self.saved.get(key)
        if hit and hit[0] == version:
            return hit[1]
        value = compute()
        self.saved[key] = (version, value)
        return value

    def close(self):
        """Close the watch connection."""
        with self.lock:
            self.watch.close()


def create_server(database, cfg, port=8000):
    """Construct a local server; also supports an ephemeral port in integration tests."""
    token = secrets.token_urlsafe(32)
    collection_lock = threading.Lock()
    state = {"running": False, "result": None}

    class Handler(BaseHTTPRequestHandler):
        """Handle only the dashboard's allowlisted routes."""

        def log_message(self, fmt, *args):
            """Route HTTP diagnostics through the application logger."""
            logger.info(fmt, *args)

        def respond(self, payload, status=200, mime="application/json; charset=utf-8"):
            """Send no-store responses with a restrictive same-origin content policy."""
            content = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            try:
                self.end_headers()
                self.wfile.write(content)
            except ConnectionError:
                logger.debug('Browser closed the request before the response completed')

        def valid_host(self):
            """Reject DNS rebinding and requests addressed to other hosts."""
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            """Read archive data or one allowlisted static asset."""
            if not self.valid_host():
                self.respond({"error": "Invalid host"}, 403)
                return
            parsed = urlsplit(self.path)
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if parsed.path in assets:
                name, mime = assets[parsed.path]
                self.respond((ROOT / "dashboard" / name).read_bytes(), mime=mime + "; charset=utf-8")
                return
            archive = Archive(database)
            try:
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                if parsed.path == "/api/bootstrap":
                    payload = {"token": token, "sources": cfg["sources"], "page_size": cfg["page_size"], "max_jobs": cfg["max_jobs"], "categories": [*categories(), "Da classificare"], "tiers": [{"id": name, "label": label(name)} for name in TIERS]}
                    from jobhunter.evaluation.selection import feedback_reasons
                    payload["feedback_reasons"] = feedback_reasons()
                elif parsed.path in ("/api/saved", "/api/metrics", "/api/proposals"):
                    from jobhunter.evaluation.selection import saved, metrics, proposals
                    payload = {"/api/saved": saved, "/api/metrics": metrics, "/api/proposals": proposals}[parsed.path](archive)
                elif parsed.path.startswith("/api/research/"):
                    from jobhunter.evaluation.selection import research_brief
                    payload = research_brief(archive, parsed.path.rsplit("/", 1)[-1])
                elif parsed.path == "/api/analytics":
                    from jobhunter.exploration.analytics import summary
                    eligibility = query.get("eligibility", "")
                    payload = cache.get(("analytics", eligibility), lambda: summary(archive, eligibility))
                elif parsed.path == "/api/pipeline":
                    from jobhunter.operations.pipeline import summary, workflow
                    from jobhunter.operations.pipeline_actions import controls, input_versions
                    # Conteggi e impronte degli input dalla cache; workflow e processi attivi sempre dal vivo.
                    payload = {**cache.get("pipeline", lambda: summary(archive, cfg)), "workflow": workflow(archive, cfg),
                               "controls": controls(archive, cfg, cache.get("versions", lambda: input_versions(archive))),
                               "collection_running": state["running"]}
                elif parsed.path == "/api/companies":
                    payload = archive.search(query.get("query", ""), query.get("status", ""), query.get("source", ""), query.get("location", ""), int(query.get("limit", cfg["page_size"])), int(query.get("offset", 0)), query.get("category", ""), query.get("eligibility", ""), query.get("tier", ""), query.get("sort") or "recenti", query.get("country", ""), query.get("city", ""))
                elif parsed.path == "/api/debug":
                    from jobhunter.exploration.debug import lenses, rows
                    payload = rows(archive, query["lens"], query.get("value", ""), int(query.get("offset", 0)),
                                   int(query.get("limit", 50)), query.get("facet_set") == "1") if query.get("lens") else lenses(archive)
                elif parsed.path == "/api/places":
                    from jobhunter.exploration.places import options
                    payload = options(archive)
                elif parsed.path.startswith("/api/company/"):
                    payload = archive.show(parsed.path.rsplit("/", 1)[-1])
                elif parsed.path == "/api/stats":
                    payload = {**archive.stats(), "collection": dict(state)}
                else:
                    self.respond({"error": "Not found"}, 404)
                    return
                self.respond(payload)
            except (ValueError, KeyError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception:
                logger.exception("Dashboard read failed")
                self.respond({"error": "Archive read failed; check server log"}, 500)
            finally:
                archive.close()

        def do_POST(self):
            """Apply validated, same-origin writes; collect in one bounded background job."""
            origin = self.headers.get("Origin")
            allowed = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
            if not self.valid_host() or self.headers.get("X-JobHunter-Token") != token or origin and origin not in allowed:
                self.respond({"error": "Invalid request origin or token"}, 403)
                return
            archive = Archive(database)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 100000:
                    raise ValueError("Request body must be between 1 and 100000 bytes")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                route = urlsplit(self.path).path
                if route == "/api/feedback":
                    result = archive.feedback(data["company_id"], data["status"], data.get("note", ""), data.get("opportunity_id"), data.get("reason", "other"), data.get("until_date") or None)
                elif route == "/api/proposal":
                    from jobhunter.evaluation.selection import proposals
                    result = proposals(archive, data["id"], data["state"])
                elif route == "/api/undo":
                    result = archive.undo(int(data["event_id"]))
                elif route == "/api/pipeline/start":
                    from jobhunter.operations.pipeline_actions import start
                    result = start(database, cfg, data['step'], data.get('parameters', {}), collection_lock, data.get('continue_after', False), data.get('remote_parameters'))
                elif route == "/api/pipeline/stop":
                    from jobhunter.operations.pipeline_actions import stop
                    result = stop(archive, data.get('job_id'))
                elif route == "/api/collect":
                    source = data["source"]
                    limit = int(data.get("limit", cfg["max_jobs"]))
                    if source not in cfg["sources"] or not 1 <= limit <= cfg["max_jobs"]:
                        raise ValueError("Unknown source or invalid acquisition limit")
                    if not collection_lock.acquire(blocking=False):
                        self.respond({"error": "Collection already running"}, 409)
                        return
                    state.update(running=True, result=None)

                    def run_collection():
                        """Own the worker connection and always release the collection lock."""
                        worker = None
                        try:
                            from jobhunter.acquisition.collection import collect
                            worker = Archive(database)
                            state["result"] = collect(worker, cfg, source, limit)
                        except Exception as exc:
                            state["result"] = {"status": "failed", "error": str(exc)}
                            logger.exception("Collection worker failed")
                        finally:
                            if worker:
                                worker.close()
                            state["running"] = False
                            collection_lock.release()
                    threading.Thread(target=run_collection, daemon=True).start()
                    result = {"started": source}
                else:
                    self.respond({"error": "Not found"}, 404)
                    return
                self.respond(result)
            except (ValueError, KeyError, TypeError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception:
                logger.exception("Dashboard write failed")
                self.respond({"error": "Save failed; check server log"}, 500)
            finally:
                archive.close()

    server = LocalHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.operation_lock = collection_lock
    # Dopo il bind: se la porta è occupata non resta una connessione aperta sull'archivio.
    server.read_cache = cache = ReadCache(database)
    return server


def serve(database, cfg, port):
    """Run the dashboard locally until interrupted."""
    try:
        server = create_server(database, cfg, port)
    except OSError:
        logger.error("Impossibile avviare il dashboard sulla porta %s. Se è già attivo, usa http://127.0.0.1:%s oppure chiudi il server esistente prima di riavviarlo.", port, port)
        raise
    logger.info("Dashboard: http://127.0.0.1:%s", server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard stopped")
    finally:
        server.server_close()
