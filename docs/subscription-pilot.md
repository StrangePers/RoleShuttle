# When the premium model only reviews the patch

The follow-up tested a lower-priced implementer, checked decimal precision beyond the original suite and calculated the API-equivalent token cost for both experiments. [Compare Sol and Luna on the same importer](model-cost-pilot.md)

A developer needs to import orders from two suppliers. One sends replacement snapshots, the other sends incremental events. Their latest contracts disagree about what a duplicate means. The importer must preserve exact amounts, handle refunds and cancellations, and survive out-of-order delivery.

We ran the same repair twice on a Codex subscription. GPT-6.2 Astra implemented and checked it alone in the first run. In the second, GPT-6.2 Sol implemented it and Astra independently reviewed the pinned result. **Both patches passed 21 held-out test groups. Astra spent 92 seconds on the solo implementation and 45 seconds on the review.** The two-model route took longer overall.

| Measured quantity | Astra alone | Sol implements, Astra reviews |
| --- | ---: | ---: |
| Held-out groups passed | 21 / 21 | 21 / 21 |
| Astra input, including cached | 119,178 | 61,212 |
| Astra cached input | 103,424 | 45,440 |
| Astra input excluding cache | 15,754 | 15,772 |
| Astra output | 1,281 | 192 |
| All models, input + output including cached input | 120,459 | 266,082 |
| Native CLI time for Astra | 92 s | 45 s |
| Native CLI time across all models | 92 s | 149 s |
| First task creation to final runtime acceptance | 144 s | 202 s |

The premium model generated 85% fewer tokens in the review role. Its gross input fell by 49%, while its input excluding cache was almost identical. Sol used 202,463 input and 2,215 output tokens. Across both models the routed attempt used 121% more tokens. Subscription charges and the quota debit per task were unavailable. None of these percentages measures a cash saving.

## How we compared the routes

- **Same starting point.** Both implementations started from the same Git revision, supplier docs, instructions, tool permissions, one editable file and three public smoke assertions. The evaluator and protocol were fixed before running either model. The original importer passed only one of the 21 held-out groups.
- **One subscription.** The released RoleShuttle 0.1.0a3 task bridge connected to a separately configured compatible runtime. The runtime launched native Codex CLI 0.166.0 using ChatGPT login, medium reasoning effort for all three calls and extra usage disabled. The standalone Ollama adapter was outside this experiment.
- **Independent review.** After Sol returned, the controller pinned its candidate and passed the changed file and both contracts to Astra in a fresh read-only task. Astra accepted it without edits. The reviewer never received the held-out answers or the solo submission. Host scripts handled handoff and external verification; no model turn was used for planning the experiment.
- **Same acceptance bar.** Scope checks limited each patch to the importer. The external oracle covered exact decimals, duplicate conflicts, later revisions, refunds, cancellations, memo events, zero totals, supplier ID collisions and unchanged inputs. It checked original and reversed order for each supplier. The public smoke check then passed on the pinned revision.
- **Measurement.** Input and output come from native CLI usage receipts. Cached input is included in input, and reasoning is included in output. Native time is the duration of the model's CLI process, including its tools. Acceptance time also includes launch, handoff, operator inspection and checks. The trials ran once each, solo first; shared cache and service conditions can affect the comparison. The ordinary chat used to design and supervise the experiment is excluded from both arms.

## Recheck the two submitted implementations

From the RoleShuttle source checkout:

```sh
python -I -B examples/reconciliation/gate.py examples/reconciliation/submitted/solo
python -I -B examples/reconciliation/gate.py examples/reconciliation/submitted/routed
```

To repeat the blind experiment with your own models, copy only the files in the seed directory into a new Git repository. Give both implementers the task in its README and access to the supplier documents. Keep the evaluator and the submitted solutions outside their worktrees. For the routed arm, give a fresh reviewer the actual patch and requirements. Record all attempts, including rejected patches and corrections.

[Task and acceptance requirements](../examples/reconciliation/seed/README.md) · [Held-out oracle](../examples/reconciliation/gate.py) · [Per-stage measurements](../examples/reconciliation/usage.json)
