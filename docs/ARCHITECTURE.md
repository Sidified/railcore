# Architecture

## Layers

```
api/ (WSGI)      cli.py
        \          /
     booking/BookingService  ── events.EventBus ──> storage (event log)
      |      |       |      \
  fares/ refunds/ tdr/   inventory/ClassInventory
      \      |       |      /
            domain/ + config (data/*.json)
                    |
            storage/ (Repository: memory | SQLite)
```

- **domain** holds plain data: `Train` with `Stop`s (distance, day offset,
  times), `TravelClass` with its `CoachLayout`, `Quota`, `Booking` with
  `PassengerRecord`s. Each record has a `booking_status` (what the ticket
  says, never changes) and a `current_status`.
- **ClassInventory** is the seat map of one class of one train run. It is
  never stored. Every service call rebuilds it from the stored passenger
  statuses (`BookingService._inventory`), changes it, and writes back only
  the passengers whose status changed.
- **BookingService** is the only writer. Each public method runs inside one
  repository transaction and publishes events; the repository subscribes
  to the bus and appends every event to the log in the same transaction.
  `DisruptionsMixin` (boarding, run cancellation, delays, TDRs) and
  `ReportsMixin` (chart report, occupancy) are split out of it by topic.

## Inventory model

Berths are split into pools by `inventory/layout.py`, the same way every
time, so a rebuilt inventory puts each berth back in its pool:

| step | pool | which berths |
|---|---|---|
| 1 | RAC | first N side lowers per coach (2 passengers each) |
| 2 | SS  | first N lower berths left |
| 3 | LD  | next N berths, first coach only |
| 4 | TQ  | N highest-numbered berths left |
| 5 | PT  | next N highest below Tatkal |
| 6 | GN  | the rest |

State inside `ClassInventory`:

- `occupant[(coach, berth)] -> PassengerRef` for confirmed passengers
- `rac_slots[(coach, berth)] -> [ref, ref]` and `rac_queue` (promotion order)
- `waitlists[GNWL|TQWL] -> [ref, ...]` (promotion order)
- `wl_issued`, `rac_issued` (numbers printed on tickets; never go down)
- `pt_sold` (drives the Premium Tatkal surge)
- `chart` (OPEN, FIRST, FINAL)

`allocate()` is all-or-nothing: it snapshots state and restores it if any
passenger of the booking cannot be placed. `release()` and
`prepare_first_chart()` return a list of `Move`s computed by diffing
every passenger's status before and after, so renumbering (RAC 3 becoming
RAC 2) is reported the same way as promotion.

**Whole-run holding.** Occupancy is per berth for the entire run. The
journey's stations are used only for the fare, the cancellation cut-off
and TDR timing, never for seat allocation.

## Charting

Charts are lazy: any call that touches a run first prepares a chart whose
time has passed (`_ensure_charts`). Operators can also force one
(`prepare_chart`). The first chart, per class:

1. gives free Tatkal berths to the Tatkal waitlist,
2. moves every free TQ, PT, LD and SS berth into the general pool,
3. fills free general berths: RAC queue first, then GNWL, then TQWL,
4. gives a lone RAC passenger the whole berth; an empty RAC berth becomes
   a normal free berth,
5. drops everyone still waitlisted, refunding the fare less clerkage.

## Storage

`Repository` is a Protocol with two implementations. SQLite applies
numbered migrations (`storage/sqlite.py`, `MIGRATIONS`) and records them in
`schema_migrations`; both stores make transactions nested-safe (only the
outermost block commits or rolls back).

## Testing

- unit tests per module (fares, layout, inventory, refunds),
- service tests run on **both** stores via a parametrised fixture,
- API tests call the WSGI app directly; CLI tests call `main()`,
- `tests/test_invariants.py` makes hundreds of random bookings and
  cancellations (and a chart) and after every step checks: no berth sold
  twice, RAC and WL numbers are 1..n with no gaps, no one waits while a
  general berth is free, refunds never exceed fares, and the inventory
  rebuilt from storage equals the stored statuses.
