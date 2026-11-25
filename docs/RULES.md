# Rules and assumptions

RailCore follows the *shape* of the Indian Railways rules (2015 Refund
Rules and later circulars). Anything RailCore decides on its own is listed
as an assumption here, so it is never mistaken for an official rule.

## Followed from the rules

- Fares: base fare by distance and class, minimum chargeable distance,
  reservation and superfast charges, Tatkal charge as a percentage of the
  base fare with a minimum and maximum, GST on AC classes.
- Advance reservation period of 60 days.
- Tatkal opens the day before the journey: 10:00 for AC classes, 11:00
  for non-AC classes. No Tatkal in 1A.
- First chart at least 8 hours before departure.
- Confirmed cancellation: flat charge more than 48 h before departure,
  25% (at least the flat charge) from 48 h to 12 h, 50% from 12 h to 4 h,
  nothing after that. GST on the charge for AC classes.
- Confirmed Tatkal: no refund. RAC/WL: clerkage up to 30 minutes before
  departure.
- Waitlisted e-tickets left after charting are cancelled with a refund
  less clerkage.
- AC failure TDR: within 20 hours of actual arrival, claim is the
  difference with the matching non-AC class (1A vs FC, 2A/3A/3E vs SL,
  CC vs 2S, EC vs FC).
- Late train (3 hours or more) and not travelled: TDR before the actual
  departure, full fare.

## RailCore assumptions

| id | assumption |
|---|---|
| A1 | All tariff numbers, quota sizes and waitlist limits in `data/` are illustrative. |
| A2 | Premium Tatkal surge: x1.00 below 25% of the pool sold, x1.25 below 50%, x1.50 below 75%, x2.00 above. Applied passenger by passenger inside one booking. |
| A3 | After a boarding-point change, cancelling a confirmed berth gives no refund. RAC/WL keep their normal clerkage refund. |
| A4 | The boarding point can be changed once, only to a later station before the destination, and only before the first chart. |
| A5 | Final chart 30 minutes before departure from the origin; after it, booking is closed. |
| A6 | Senior lower-berth quota: men 60+, women 45+, transgender 60+; at most 2 passengers; falls back to general allocation when the SS berths run out. |
| A7 | Ladies quota is for female passengers only (no accompanying children modelled) and has no waitlist. |
| A8 | Premium Tatkal has no waitlist and no RAC. Tatkal has a waitlist (TQWL) but no RAC. |
| A9 | At charting, the general waitlist is cleared before the Tatkal waitlist. |
| A10 | Children under 5 are not booked (they travel free without a berth). |
| A11 | A RAC passenger whose berth partner cancels after charting gets the whole berth. |
| A12 | Cancelling a whole train run refunds the full fare to every active passenger, including RAC and waitlisted. |
| A13 | Cancellation and TDR cut-offs use the departure from the passenger's current boarding station. |
