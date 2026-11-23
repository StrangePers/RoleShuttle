# What does routing cost on the same task?

**CSV export.** GPT-6 Luna and GPT-6.2 Astra independently made the same two configuration changes. Both passed the project check and all seven held-out cases. Pricing their recorded tokens at Standard API rates gives **$0.0032 with Luna and $0.4363 with Astra** (99.3% lower for Luna). Each model ran once, Luna first. Native CLI times were 56 and 227 seconds respectively.

**Order reconciliation.** Luna passed all 21 original test groups but rounded a valid order amount incorrectly. A fresh Astra reviewer independently reproduced the issue and supplied a corrected implementation. The reviewed result passed the original suite and both additional precision cases. Including the correction, the estimated token cost was **$0.2104**, versus **$0.2186** when Astra implemented the importer alone (3.8% lower). Sol with Astra review came to **$0.2712** (24.0% higher than Astra alone).

| Task and model assignment | API-equivalent token cost | Outcome |
| --- | ---: | --- |
| CSV, Luna alone | $0.0032 | Same options, 7/7 held-out cases |
| CSV, Astra alone | $0.4363 | Same options, 7/7 held-out cases |
| Importer, Astra alone | $0.2186 | 21/21 original groups, 2/2 precision cases |
| Importer, Sol + Astra | $0.2712 | 21/21 and 2/2, accepted without edits |
| Importer, Luna implementation stage | $0.0060 | 21/21 but 0/2 precision cases |
| Importer, Luna + Astra correction | $0.2104 | 21/21 and 2/2, corrected code accepted |

The $0.0060 implementation is included in the $0.2104 complete route. The second Astra review was run on Luna's actual patch. The earlier Sol review was a different call and is charged only to the Sol route.

## Why the results differ

The CSV exporter already had a selection filter and a CSV writer. With the output contract specified, the job was to select two existing settings. The inexpensive model could finish the entire job and the same deterministic tests could verify the output. These trials measure the benefit of assigning that job to Luna. A native client that calls Luna directly could also use the lower-priced model.

The importer had suppliers with different revision and deduplication rules, plus a requirement for exact amounts. In Luna's patch, multiplying a Decimal by 100 used the active precision. At six-digit precision it converted 12,345.67 to 1,234,570 minor units instead of 1,234,567. We added two diagnostic cases after inspecting the first patch and applied them to every submission. They were withheld from Astra's review. The reviewer found the error with its own large-number example. Its corrected code uses an exact integer ratio. Native CLI time was 117 s for Luna and 64 s for the review, versus 92 s for Astra alone.

## What the estimate includes

Live runs used Codex on a ChatGPT subscription through the RoleShuttle 0.1.0a3 task bridge and an operator-configured runtime. Extra usage was disabled. The baseline and review used Astra medium, the earlier implementation used Sol medium, and the follow-up used Luna low. Each assignment was tested once. The machine, seed, allowed file and checks matched within each comparison. Run order, cache reuse, model sampling and effort can affect the result. Subscription quota debits and actual per-task bills were unavailable.

| OpenAI Standard text rate on 2026-11-23, USD per million | Fresh input | Cached input | Output |
| --- | ---: | ---: | ---: |
| GPT-6 Luna | $0.10 | $0.01 | $0.50 |
| GPT-6.2 Sol | $2.00 | $0.10 | $10.00 |
| GPT-6.2 Astra | $8.00 | $0.40 | $40.00 |

For each stage, subtract cached input from total input, apply the three rates separately, divide by one million and sum across the route. Astra's CSV session reported 682,630 input tokens cumulatively, including 665,344 cached. Its largest individual request was 31,000 input tokens. Every other stage was under 272,000 input tokens even in aggregate. All requests therefore fit short-context rates. Cache-write counts were zero. Output already includes reasoning. These are token-price estimates for recorded CLI workloads; subscription quota and money debited were not measured. [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)

## Verify the comparison

From the source checkout, reprice the runs and check the corrected implementation:

```sh
python -I -B examples/reconciliation/price_pilot.py
python -I -B examples/reconciliation/gate.py examples/reconciliation/submitted/luna-reviewed
python -I -B examples/reconciliation/precision_probe.py examples/reconciliation/submitted/luna-reviewed
```

For the CSV comparison, start from the existing orders example at revision `9ebff7d853af924e03b36f661be517acdfea59c6`. Both patches set `scope` to `selected`, `encoding` to `csv` and keep the semicolon delimiter. Run the project's export check and the held-out CSV script against that checkout. The cases include an unknown ID, empty input and selection, reversed selection order, an embedded separator, CRLF, quoted fields and an empty customer name.

[Recorded usage by model and task](../examples/reconciliation/usage.json) · [CSV holdout](../examples/csv_holdout.py) · [Original paired importer trial](subscription-pilot.md)
