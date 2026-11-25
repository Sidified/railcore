# RailCore

A reservation engine modelled on how Indian Railways sells berths: fares,
quotas, RAC and waitlists, charting, cancellations, refunds and TDRs.
Pure Python 3.11+, no runtime dependencies, fully offline.

RailCore is a learning and portfolio project. Its tariff and quota numbers
are illustrative, not the official ones (see [docs/RULES.md](docs/RULES.md)).
It was built by Sid Hary with help from an AI assistant (Claude); every
change is in the git history.

## What it does

- **Fares**: telescopic per-km base fare, reservation and superfast
  charges, Tatkal charge with class minimum and maximum, a Premium Tatkal
  surge that rises as the PT pool fills, GST on AC classes, totals rounded
  up to the next 5 rupees.
- **Inventory**: coaches and berths per class, split into quota pools
  (general, Tatkal, Premium Tatkal, ladies, senior lower berth) and RAC
  side-lower berths shared by two passengers.
- **Waitlists**: GNWL and TQWL with limits ("REGRET" when full). A ticket
  keeps its issued number ("GNWL 23"); the current status shows the
  queue position ("GNWL 12").
- **Promotion**: a cancelled confirmed berth goes to RAC 1, whose RAC
  place goes to WL 1. Tatkal berths go to the Tatkal waitlist first.
- **Charting** (lazy, at 8 hours and 30 minutes before departure from the
  origin): unused quota berths are released, queues are cleared, a lone
  RAC passenger gets the whole berth, and passengers still waitlisted are
  dropped with a refund less clerkage. After the first chart only
  current (general) booking is open.
- **Cancellation refunds** by time window, Tatkal rules and RAC/WL clerkage.
- **After sale**: boarding-point change, cancelling a whole train run,
  delay reports, TDR claims (late train, AC failure, RAC not travelled)
  and their settlement.
- **Storage**: SQLite with numbered migrations, or in memory. Every
  change is written to an append-only event log.
- **Interfaces**: a JSON HTTP API (plain WSGI) and a CLI.

## Quick start

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

CLI (prints JSON; `--now` fixes the clock, `--db` picks the database):

```
railcore --now 2026-12-01T09:00 availability 22401 2026-12-10 SL
railcore --now 2026-12-01T09:00 fare 22401 2026-12-10 NDLS BPL SL --passengers 2
railcore --now 2026-12-01T09:00 book 22401 2026-12-10 NDLS BPL SL -p "Asha,30,F,LB" -p "Ravi,32,M"
railcore pnr <PNR>
railcore --now 2026-12-01T09:00 cancel <PNR> --passengers 2
railcore chart-report 22401 2026-12-10 SL
railcore serve --port 8000
```

API: see the docstring at the top of `railcore/api/app.py` for every route.

```
curl -X POST localhost:8000/bookings -H 'Idempotency-Key: order-1' -d '{
  "train": "22401", "date": "2026-12-10", "from": "NDLS", "to": "BPL", "class": "SL",
  "passengers": [{"name": "Asha", "age": 30, "gender": "F", "berth_preference": "LB"}]}'
```

## Sample network

Four trains over central India (`data/trains.json`): 22401 Satpura
Superfast (NDLS-NGP, daily), 12099 Narmada Rajdhani (NDLS-JBP, Mon/Wed/Fri),
12001 Bhopal Shatabdi (NDLS-BPL, daily) and 11071 Kamayani Express
(CSMT-JHS, daily). Dates passed to RailCore are the date the train leaves
its **origin**.

## Project layout

```
railcore/
  domain/       classes, quotas, network (stations, trains), bookings
  fares/        fare calculator
  inventory/    berth layout and quota pools; ClassInventory (allocate, release, chart)
  refunds/      cancellation policy
  charting/     chart timing
  tdr/          TDR model and policy
  booking/      BookingService (+ disruptions and reports mixins), validation
  storage/      repository interface, in-memory and SQLite stores
  api/          WSGI app, serialisers, dev server
  cli.py        command line
data/           network, tariff, quotas and policy as JSON
docs/           architecture, rules and assumptions, decisions log
tests/          unit, service, API, CLI and randomised invariant tests
```

## Known limitations

- **A berth is held for the whole run.** A passenger going NDLS to AGC
  blocks their berth all the way to NGP; nobody can be sold that berth
  for AGC onwards. Real reservation systems sell the same berth for
  non-overlapping parts of the route.
- Availability is per train and class, not per journey segment.
- One process at a time per database file (SQLite locking, no server).
- The tariff, quota sizes and some policy details are RailCore
  assumptions, listed in `docs/RULES.md`.

## Licence

MIT
