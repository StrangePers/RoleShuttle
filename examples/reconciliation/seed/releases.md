# Compatibility notes for the active release

North now sends cancelled snapshots. Like pending snapshots, these supersede older paid data and remove the order from output. Amount is present even on pending or cancelled snapshots. A conflict at a lower revision has no effect on a higher revision.

For Coast, compare duplicate deliveries by (kind, minor) for monetary events. Same order and same event ID with conflicting monetary payloads invalidates that event only. Other valid events for that order continue to contribute. Memo deliveries do not reserve event IDs and cannot conflict with monetary events. Two orders may reuse an event ID. Distinct suppliers may reuse an order ID.

No persistence, external services, rounding policy for invalid inputs, or currency conversion is needed for this release.
