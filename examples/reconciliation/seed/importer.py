"""Normalize two supplier feeds for the daily order reconciliation CLI."""
def reconcile(north, coast):
    totals = {}
    for row in north:
        if row.get("state") == "paid":
            totals[row["id"]] = int(float(row["total"]) * 100)
    for row in coast:
        if row.get("kind") == "capture":
            totals[row["ref"]] = row["minor"]
    return totals
