"""Normalize two supplier feeds for the daily order reconciliation CLI."""

from decimal import Decimal


def reconcile(north, coast):
    snapshots = {}
    for row in north:
        order_id = row["id"]
        seq = row["seq"]
        current = snapshots.get(order_id)
        if current is not None and seq < current[0]:
            continue

        # Integer arithmetic keeps the conversion independent of Decimal precision.
        numerator, denominator = Decimal(row["total"]).as_integer_ratio()
        payload = (row["state"], numerator * 100 // denominator)
        if current is None or seq > current[0]:
            snapshots[order_id] = (seq, payload)
        elif payload != current[1]:
            snapshots[order_id] = (seq, None)

    totals = {}
    for order_id, (_, payload) in snapshots.items():
        if payload is None:
            continue
        state, minor = payload
        if state == "paid":
            totals[f"north/{order_id}"] = minor
        elif state == "refunded":
            totals[f"north/{order_id}"] = -minor

    events = {}
    for row in coast:
        kind = row["kind"]
        if kind == "memo":
            continue

        event_key = (row["ref"], row["eid"])
        payload = (kind, row["minor"])
        if events.setdefault(event_key, payload) != payload:
            events[event_key] = None

    for (order_id, _), payload in events.items():
        if payload is None:
            continue
        kind, minor = payload
        key = f"coast/{order_id}"
        totals[key] = totals.get(key, 0) + (minor if kind == "capture" else -minor)

    return totals
