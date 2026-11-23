"""Normalize two supplier feeds for the daily order reconciliation CLI."""


def _minor_units(amount):
    whole, _, fraction = amount.partition(".")
    return int(whole or "0") * 100 + int(fraction.ljust(2, "0"))


def reconcile(north, coast):
    snapshots = {}
    for row in north:
        order, seq = row["id"], row["seq"]
        payload = row["state"], _minor_units(row["total"])
        previous = snapshots.get(order)
        if previous is None or seq > previous[0]:
            snapshots[order] = seq, payload
        elif seq == previous[0] and payload != previous[1]:
            snapshots[order] = seq, None

    totals = {}
    for order, (_, payload) in snapshots.items():
        if payload is None:
            continue
        state, minor = payload
        if state in ("paid", "refunded"):
            totals[f"north/{order}"] = minor if state == "paid" else -minor

    events = {}
    for row in coast:
        kind = row["kind"]
        if kind == "memo":
            continue
        key = row["ref"], row["eid"]
        payload = kind, row["minor"]
        if key not in events:
            events[key] = payload
        elif events[key] != payload:
            events[key] = None

    for (order, _), payload in events.items():
        if payload is None:
            continue
        kind, minor = payload
        key = f"coast/{order}"
        totals[key] = totals.get(key, 0) + (minor if kind == "capture" else -minor)
    return totals
