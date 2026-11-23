"""Normalize two supplier feeds for the daily order reconciliation CLI."""

from decimal import Decimal


def reconcile(north, coast):
    """Return signed minor-unit totals keyed by supplier and order ID."""
    totals = {}

    # Keep only the greatest snapshot version, while tracking conflicts at
    # each version so a conflict at an older version cannot poison a newer one.
    snapshots = {}
    for row in north:
        order_id = row["id"]
        seq = row["seq"]
        state = row["state"]
        amount = Decimal(row["total"])
        business = (state, amount)
        current = snapshots.get(order_id)
        if current is None or seq > current[0]:
            snapshots[order_id] = (seq, business, False)
        elif seq == current[0] and business != current[1]:
            snapshots[order_id] = (seq, current[1], True)

    for order_id, (seq, business, conflict) in snapshots.items():
        if conflict:
            continue
        state, amount = business
        if state == "paid":
            totals["north/" + order_id] = int(amount * 100)
        elif state == "refunded":
            totals["north/" + order_id] = -int(amount * 100)

    # Monetary event IDs are unique per order. Identical repeats are harmless;
    # conflicting payloads invalidate that event only. Memos do not reserve IDs.
    events = {}
    for row in coast:
        kind = row["kind"]
        if kind not in ("capture", "refund"):
            continue
        order_id = row["ref"]
        event_id = row["eid"]
        payload = (kind, row["minor"])
        order_events = events.setdefault(order_id, {})
        current = order_events.get(event_id)
        if current is None:
            order_events[event_id] = [payload, False]
        elif current[0] != payload:
            current[1] = True

    for order_id, order_events in events.items():
        contributed = False
        net = 0
        for payload, conflict in order_events.values():
            if conflict:
                continue
            kind, minor = payload
            contributed = True
            net += minor if kind == "capture" else -minor
        if contributed:
            totals["coast/" + order_id] = net

    return totals
