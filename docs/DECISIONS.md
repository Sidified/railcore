# Decisions

Short log of design choices, newest last.

1. **Decimal money everywhere.** Fares are rounded up to the next 5 rupees
   only at the end, so each component stays exact.
2. **Data in JSON, rules in code.** Tariff, quotas and policy numbers live
   in `data/`; how they are applied lives in code. Changing a number never
   needs a code change.
3. **Inventory is derived, not stored.** Storing passenger statuses only
   (not a separate seat table) means one source of truth. The cost is
   rebuilding the inventory on each call; the randomised test checks the
   rebuild always matches.
4. **Deterministic pool split.** Pools are recomputed, never saved, so the
   split must be identical every time (`inventory/layout.py`).
5. **Ticket number vs current number.** The ticket keeps the issued RAC/WL
   number; the current status is the queue position. The randomised test
   found a bug where a new RAC passenger got the issued number as their
   current number.
6. **Lazy charting.** No scheduler: whichever call first touches a run
   after chart time prepares the chart, inside the same transaction.
7. **Moves by diff.** Promotions and renumbering are found by comparing
   all statuses before and after a change, so no code path can forget to
   report a changed passenger.
8. **Whole-run berth holding** kept for now (see README, Known
   limitations). It is the biggest gap against a real system.
9. **Mixins for after-sale features.** Boarding change, run cancellation,
   delays and TDRs share the service's transaction and inventory helpers
   but live in `booking/disruptions.py` to keep `service.py` readable.
10. **Idempotency keys** on booking, so a client retrying after a timeout
    cannot book twice; a key reused for a different request is an error.
