"""A small threaded HTTP(S) server for the WSGI app (standard library only).

For an internal tool used by a handful of people this is sufficient; for TLS you can either pass a
certificate (--cert/--key) or put nginx in front (see deploy/nginx-license-ui.conf) and use --trust-proxy.
"""
from __future__ import annotations

import logging
import logging.handlers
import signal
import ssl
import threading
from socketserver import ThreadingMixIn
from typing import Any, Optional
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

log = logging.getLogger("lic_web")


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = 64


class QuietHandler(WSGIRequestHandler):
    timeout = 60                      # a stalled client cannot hold a thread forever

    def log_message(self, format: str, *args: Any) -> None:   # the app logs every request itself
        pass


def configure_logging(log_file: Optional[str], verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def serve(app: Any, host: str, port: int, certfile: Optional[str] = None, keyfile: Optional[str] = None) -> None:
    server = make_server(host, port, app, server_class=ThreadedWSGIServer, handler_class=QuietHandler)
    scheme = "http"
    if certfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile, keyfile)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        server.base_environ["HTTPS"] = "on"
        scheme = "https"
    stop = threading.Event()

    def _shutdown(signum: int, frame: Any) -> None:
        log.info("signal %s received - shutting down", signum)
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _shutdown)
            except ValueError:
                pass
    log.info("Serving on %s://%s:%d", scheme, host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            app.jobs.stop()
        except Exception:
            pass
