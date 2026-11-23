# Coast / ledger API

Coast sends deltas. ref is the order identifier, eid is the delivery event identifier scoped to that order, kind is capture, refund, or memo, and minor is a non-negative integer in minor units. The public key is coast. Sum captures positively and refunds negatively. Memo entries are informational, and their minor field may be absent. Omit orders with no contributing ledger event. Keep a zero net total for an order with at least one accepted capture or refund.

Delivery order is not significant. Revised Coast deduplication rules are in the compatibility notes. The APIs guarantee valid types and required fields for each kind. Unknown fields must not affect reconciliation.
