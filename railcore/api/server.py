"""Run the API with the standard-library WSGI server (for local use only)."""
from __future__ import annotations

from wsgiref.simple_server import make_server

from ..booking import BookingService
from ..storage import SQLiteRepository
from .app import RailCoreApp


def serve(db_path: str = "railcore.db", host: str = "127.0.0.1", port: int = 8000) -> None:
    app = RailCoreApp(BookingService(SQLiteRepository(db_path)))
    with make_server(host, port, app) as httpd:
        print(f"RailCore API on http://{host}:{port} (database: {db_path})")
        httpd.serve_forever()
