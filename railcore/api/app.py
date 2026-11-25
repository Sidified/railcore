"""A small JSON HTTP API as a plain WSGI app (no framework needed).

    GET  /health
    GET  /trains                         ?from=BPL&to=JHS
    GET  /trains/{number}
    GET  /availability                   ?train=&date=&class=&quota=
    GET  /fare                           ?train=&date=&from=&to=&class=&quota=&passengers=
    POST /bookings                       {train, date, from, to, class, quota, passengers: [...]}
    GET  /bookings/{pnr}
    GET  /bookings/{pnr}/refund          ?passengers=1,2
    POST /bookings/{pnr}/cancel          {passengers: [1, 2]}   (omit to cancel all)
    POST /runs/{train}/{date}/chart      {kind: "first" | "final"}
    GET  /events                         ?after=0&limit=100
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import date
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from ..booking import BookingRequest, BookingService, PassengerInput
from ..errors import (BookingClosed, InvalidState, NoAvailability, NotFound, RailCoreError,
                      ValidationError)
from . import serialize

STATUS_FOR_ERROR: dict[type, HTTPStatus] = {
    ValidationError: HTTPStatus.BAD_REQUEST,
    NotFound: HTTPStatus.NOT_FOUND,
    BookingClosed: HTTPStatus.CONFLICT,
    InvalidState: HTTPStatus.CONFLICT,
    NoAvailability: HTTPStatus.CONFLICT,
}


class HTTPError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Request:
    def __init__(self, environ: dict):
        self.method = environ["REQUEST_METHOD"].upper()
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        self._environ = environ

    def json(self) -> dict[str, Any]:
        try:
            length = int(self._environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        raw = self._environ["wsgi.input"].read(length) if length else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_json", "request body is not valid JSON") from None
        if not isinstance(body, dict):
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_json", "request body must be a JSON object")
        return body

    def arg(self, name: str, default: str | None = None) -> str:
        value = self.query.get(name, default)
        if value is None:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "missing_parameter", f"query parameter {name!r} is required")
        return value


def _date(value: Any, field: str = "date") -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_date", f"{field} must be YYYY-MM-DD") from None


def _required(body: dict, *names: str) -> None:
    missing = [n for n in names if n not in body]
    if missing:
        raise HTTPError(HTTPStatus.BAD_REQUEST, "missing_field", f"missing field(s): {', '.join(missing)}")


def _int_list(value: Any, field: str) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [v for v in value.split(",") if v.strip()]
    try:
        return [int(v) for v in value]
    except (TypeError, ValueError):
        raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_field", f"{field} must be a list of numbers") from None


Handler = Callable[..., tuple[HTTPStatus, Any]]


class RailCoreApp:
    def __init__(self, service: BookingService):
        self.service = service
        self.routes: list[tuple[str, re.Pattern, Handler]] = []
        add = self._add
        add("GET", r"/health", self.health)
        add("GET", r"/trains", self.list_trains)
        add("GET", r"/trains/(?P<number>\d+)", self.get_train)
        add("GET", r"/availability", self.availability)
        add("GET", r"/fare", self.fare)
        add("POST", r"/bookings", self.create_booking)
        add("GET", r"/bookings/(?P<pnr>\d{10})", self.get_booking)
        add("GET", r"/bookings/(?P<pnr>\d{10})/refund", self.refund_quote)
        add("POST", r"/bookings/(?P<pnr>\d{10})/cancel", self.cancel)
        add("POST", r"/runs/(?P<train>\d+)/(?P<day>\d{4}-\d{2}-\d{2})/chart", self.chart)
        add("GET", r"/events", self.events)

    def _add(self, method: str, pattern: str, handler: Handler) -> None:
        self.routes.append((method, re.compile(f"^{pattern}/?$"), handler))

    # ---------------------------------------------------------------- WSGI
    def __call__(self, environ: dict, start_response) -> Iterable[bytes]:
        request = Request(environ)
        try:
            status, body = self.dispatch(request)
        except HTTPError as exc:
            status, body = exc.status, {"error": exc.code, "message": exc.message}
        except RailCoreError as exc:
            status = STATUS_FOR_ERROR.get(type(exc), HTTPStatus.BAD_REQUEST)
            body = {"error": exc.code, "message": str(exc)}
        payload = json.dumps(body, indent=2, sort_keys=True).encode()
        start_response(f"{status.value} {status.phrase}", [
            ("Content-Type", "application/json"), ("Content-Length", str(len(payload)))])
        return [payload]

    def dispatch(self, request: Request) -> tuple[HTTPStatus, Any]:
        allowed = []
        for method, pattern, handler in self.routes:
            match = pattern.match(request.path)
            if match:
                if method == request.method:
                    return handler(request, **match.groupdict())
                allowed.append(method)
        if allowed:
            raise HTTPError(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed",
                            f"use {' or '.join(sorted(set(allowed)))} for {request.path}")
        raise HTTPError(HTTPStatus.NOT_FOUND, "not_found", f"no route for {request.path}")

    # ------------------------------------------------------------ handlers
    def health(self, request: Request):
        return HTTPStatus.OK, {"status": "ok"}

    def list_trains(self, request: Request):
        net = self.service.network
        frm, to = request.query.get("from"), request.query.get("to")
        if frm and to:
            trains = net.trains_between(frm, to)
        else:
            trains = sorted(net.trains.values(), key=lambda t: t.number)
        return HTTPStatus.OK, [serialize.train(t) for t in trains]

    def get_train(self, request: Request, number: str):
        return HTTPStatus.OK, serialize.train(self.service.network.train(number))

    def availability(self, request: Request):
        text = self.service.availability(request.arg("train"), _date(request.arg("date")),
                                         request.arg("class"), request.arg("quota", "GN"))
        return HTTPStatus.OK, {"availability": text}

    def fare(self, request: Request):
        try:
            count = int(request.arg("passengers", "1"))
        except ValueError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_parameter", "passengers must be a number") from None
        if not 1 <= count <= 6:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_parameter", "passengers must be 1 to 6")
        fares = self.service.quote(request.arg("train"), _date(request.arg("date")), request.arg("from"),
                                   request.arg("to"), request.arg("class"), request.arg("quota", "GN"), count)
        return HTTPStatus.OK, {"fares": [serialize.fare(f) for f in fares],
                               "total": str(sum(f.total for f in fares))}

    def create_booking(self, request: Request):
        body = request.json()
        _required(body, "train", "date", "from", "to", "class", "passengers")
        if not isinstance(body["passengers"], list):
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_field", "passengers must be a list")
        passengers = []
        for i, p in enumerate(body["passengers"], 1):
            if not isinstance(p, dict) or not {"name", "age", "gender"} <= p.keys():
                raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_field",
                                f"passenger {i} needs name, age and gender")
            try:
                age = int(p["age"])
            except (TypeError, ValueError):
                raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_field", f"passenger {i} age must be a number") from None
            passengers.append(PassengerInput(str(p["name"]), age, str(p["gender"]), p.get("berth_preference")))
        booking = self.service.book(BookingRequest(
            str(body["train"]), _date(body["date"]), str(body["from"]), str(body["to"]),
            str(body["class"]), str(body.get("quota", "GN")), passengers))
        return HTTPStatus.CREATED, serialize.booking(booking)

    def get_booking(self, request: Request, pnr: str):
        return HTTPStatus.OK, serialize.booking(self.service.get_booking(pnr))

    def refund_quote(self, request: Request, pnr: str):
        chosen = _int_list(request.query.get("passengers"), "passengers")
        quotes = self.service.refund_quote(pnr, chosen)
        return HTTPStatus.OK, {"refunds": {str(i): serialize.refund(q) for i, q in quotes.items()}}

    def cancel(self, request: Request, pnr: str):
        body = request.json()
        result = self.service.cancel(pnr, _int_list(body.get("passengers"), "passengers"))
        return HTTPStatus.OK, {
            "booking": serialize.booking(result.booking),
            "refunds": {str(i): serialize.refund(q) for i, q in result.refunds.items()},
            "total_refund": str(result.total_refund),
            "others_moved": [{"pnr": m.ref.pnr, "passenger": m.ref.index, "status": m.status.label()}
                             for m in result.moves],
        }

    def chart(self, request: Request, train: str, day: str):
        body = request.json()
        result = self.service.prepare_chart(train, _date(day), str(body.get("kind", "first")))
        return HTTPStatus.OK, {
            "train": result.train_number, "start_date": result.start_date.isoformat(), "kind": result.kind,
            "moved": len(result.moves), "dropped": [str(r) for r in result.dropped],
            "released": result.released,
        }

    def events(self, request: Request):
        try:
            after = int(request.arg("after", "0"))
            limit = min(int(request.arg("limit", "100")), 1000)
        except ValueError:
            raise HTTPError(HTTPStatus.BAD_REQUEST, "bad_parameter", "after and limit must be numbers") from None
        return HTTPStatus.OK, [e.as_dict() for e in self.service.repo.events(after, limit)]
