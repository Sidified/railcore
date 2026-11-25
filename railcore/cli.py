"""Command-line interface. Prints JSON.

    railcore trains --from BPL --to JHS
    railcore availability 22401 2026-12-10 SL
    railcore fare 22401 2026-12-10 NDLS BPL SL --passengers 2
    railcore book 22401 2026-12-10 NDLS BPL SL -p "Asha,30,F,LB" -p "Ravi,32,M"
    railcore pnr 2000000014
    railcore cancel 2000000014 --passengers 2
    railcore chart 22401 2026-12-10 --kind first
    railcore boarding 2000000014 AGC
    railcore cancel-run 22401 2026-12-10 --reason "track work"
    railcore delay 22401 2026-12-10 BPL --departure 200
    railcore tdr 2000000014 TRAIN_LATE_NOT_TRAVELLED --passengers 1
    railcore settle-tdr 1 --approve
    railcore chart-report 22401 2026-12-10 3A
    railcore events --after 0
    railcore serve --port 8000

Use --db to pick the database file and --now "2026-12-01T09:00" to fix the clock.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from typing import Any

from .api import serialize
from .booking import BookingRequest, BookingService, PassengerInput
from .clock import FixedClock, SystemClock
from .errors import RailCoreError
from .storage import InMemoryRepository, SQLiteRepository


def _passenger(text: str) -> PassengerInput:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) not in (3, 4):
        raise argparse.ArgumentTypeError("passenger must be NAME,AGE,GENDER[,BERTH]")
    try:
        age = int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(f"age must be a number in {text!r}") from None
    return PassengerInput(parts[0], age, parts[2], parts[3] if len(parts) == 4 else None)


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from None


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("time must be YYYY-MM-DDTHH:MM") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="railcore", description="RailCore reservation engine")
    parser.add_argument("--db", default="railcore.db", help="SQLite file (default railcore.db)")
    parser.add_argument("--memory", action="store_true", help="use a throwaway in-memory store")
    parser.add_argument("--now", type=_when, help="pretend the current IST time is this")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("trains", help="list trains")
    p.add_argument("--from", dest="frm")
    p.add_argument("--to")

    p = sub.add_parser("availability", help="seat availability")
    p.add_argument("train"), p.add_argument("date", type=_date), p.add_argument("travel_class")
    p.add_argument("--quota", default="GN")

    p = sub.add_parser("fare", help="fare quote")
    for name in ("train",):
        p.add_argument(name)
    p.add_argument("date", type=_date)
    p.add_argument("frm", metavar="from"), p.add_argument("to"), p.add_argument("travel_class")
    p.add_argument("--quota", default="GN")
    p.add_argument("--passengers", type=int, default=1)

    p = sub.add_parser("book", help="make a booking")
    p.add_argument("train"), p.add_argument("date", type=_date)
    p.add_argument("frm", metavar="from"), p.add_argument("to"), p.add_argument("travel_class")
    p.add_argument("--quota", default="GN")
    p.add_argument("-p", "--passenger", type=_passenger, action="append", required=True,
                   help="NAME,AGE,GENDER[,BERTH]; repeat for each passenger")

    p = sub.add_parser("pnr", help="PNR status")
    p.add_argument("pnr")

    p = sub.add_parser("cancel", help="cancel passengers (all if none given)")
    p.add_argument("pnr")
    p.add_argument("--passengers", type=lambda s: [int(x) for x in s.split(",")])
    p.add_argument("--quote", action="store_true", help="only show the refund, do not cancel")

    p = sub.add_parser("chart", help="prepare a chart now")
    p.add_argument("train"), p.add_argument("date", type=_date)
    p.add_argument("--kind", choices=["first", "final"], default="first")

    p = sub.add_parser("boarding", help="change the boarding station")
    p.add_argument("pnr"), p.add_argument("station")

    p = sub.add_parser("cancel-run", help="cancel a whole train run (full refunds)")
    p.add_argument("train"), p.add_argument("date", type=_date)
    p.add_argument("--reason", default="")

    p = sub.add_parser("delay", help="report a delay at a station (minutes)")
    p.add_argument("train"), p.add_argument("date", type=_date), p.add_argument("station")
    p.add_argument("--arrival", type=int, default=0)
    p.add_argument("--departure", type=int, default=0)

    p = sub.add_parser("tdr", help="file a TDR")
    p.add_argument("pnr")
    p.add_argument("reason", choices=["TRAIN_LATE_NOT_TRAVELLED", "AC_FAILURE", "RAC_NOT_TRAVELLED"])
    p.add_argument("--passengers", type=lambda s: [int(x) for x in s.split(",")])

    p = sub.add_parser("settle-tdr", help="approve or reject a TDR")
    p.add_argument("tdr_id", type=int)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    p.add_argument("--note", default="")

    p = sub.add_parser("chart-report", help="coach-wise reservation chart")
    p.add_argument("train"), p.add_argument("date", type=_date), p.add_argument("travel_class")

    p = sub.add_parser("events", help="show the event log")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("serve", help="run the JSON API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    return parser


def run(args: argparse.Namespace, service: BookingService) -> Any:
    c = args.command
    if c == "trains":
        net = service.network
        trains = net.trains_between(args.frm, args.to) if args.frm and args.to else \
            sorted(net.trains.values(), key=lambda t: t.number)
        return [serialize.train(t) for t in trains]
    if c == "availability":
        return {"availability": service.availability(args.train, args.date, args.travel_class, args.quota)}
    if c == "fare":
        fares = service.quote(args.train, args.date, args.frm, args.to, args.travel_class, args.quota,
                              args.passengers)
        return {"fares": [serialize.fare(f) for f in fares], "total": str(sum(f.total for f in fares))}
    if c == "book":
        booking = service.book(BookingRequest(args.train, args.date, args.frm, args.to, args.travel_class,
                                              args.quota, args.passenger))
        return serialize.booking(booking)
    if c == "pnr":
        return serialize.booking(service.get_booking(args.pnr))
    if c == "cancel":
        if args.quote:
            quotes = service.refund_quote(args.pnr, args.passengers)
            return {str(i): serialize.refund(q) for i, q in quotes.items()}
        result = service.cancel(args.pnr, args.passengers)
        return {"booking": serialize.booking(result.booking), "total_refund": str(result.total_refund),
                "refunds": {str(i): serialize.refund(q) for i, q in result.refunds.items()}}
    if c == "chart":
        result = service.prepare_chart(args.train, args.date, args.kind)
        return {"kind": result.kind, "moved": len(result.moves),
                "dropped": [str(r) for r in result.dropped], "released": result.released}
    if c == "boarding":
        return serialize.booking(service.change_boarding(args.pnr, args.station))
    if c == "cancel-run":
        return {"refunded_passengers": service.cancel_run(args.train, args.date, args.reason)}
    if c == "delay":
        service.report_delay(args.train, args.date, args.station, args.arrival, args.departure)
        return {"station": args.station, "arrival_delay": args.arrival, "departure_delay": args.departure}
    if c == "tdr":
        return serialize.tdr(service.file_tdr(args.pnr, args.reason, args.passengers))
    if c == "settle-tdr":
        return serialize.tdr(service.settle_tdr(args.tdr_id, args.approve, args.note))
    if c == "chart-report":
        return service.chart_report(args.train, args.date, args.travel_class)
    if c == "events":
        return [e.as_dict() for e in service.repo.events(args.after, args.limit)]
    raise AssertionError(c)  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from .api.server import serve
        serve(args.db, args.host, args.port)
        return 0
    repo = InMemoryRepository() if args.memory else SQLiteRepository(args.db)
    clock = FixedClock(args.now) if args.now else SystemClock()
    service = BookingService(repo, clock=clock)
    try:
        out = run(args, service)
    except RailCoreError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
