# Which route should fix a two-setting CSV export?

Luna alone was the cheapest route that passed the checks. Asking Astra to review Luna’s settings added 4.1% to the estimated token cost compared with asking Astra to implement the change alone. The reviewer approved the same settings on all three attempts.

| Route | Accepted | Mean input tokens | Mean output tokens | Mean elapsed | Elapsed range | API USD per accepted fix |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Luna alone | 3/3 | 19,315 | 32 | 7.93 s | 7.05–8.70 s | $0.001948 |
| Astra alone | 3/3 | 21,316 | 32 | 8.77 s | 7.80–9.64 s | $0.171808 |
| Luna + Astra review | 3/3 | 40,912 | 133.7 | 17.99 s | 17.23–19.24 s | $0.178790 |

## Task and acceptance

Change the fictional exporter to include selected orders only and preserve quotes, semicolons and newlines inside customer names. Keep the original delimiter. Each implementer received identical requirements, initial settings and output instructions. Every route produced `{"scope":"selected","encoding":"csv","delimiter":";"}`.

Before each timed attempt, the unchanged fixture failed the seven-case CSV checker. The driver applied the submitted settings to a fresh copy and ran the same checker after each attempt. It parsed the actual exports, including empty input, selection ordering, repeated IDs and multiline fields. All nine final configurations passed. For the reviewed route, acceptance also required an explicit verdict from a fresh Astra thread that received the requirements, Luna’s actual proposal and the exporter source. The seven test cases stayed outside the prompts.

## What was measured

On November 23, 2026 we used the public RoleShuttle Codex stage adapter, Codex CLI 0.166.0 and a ChatGPT subscription on Windows. All three routes used the same adapter and implementation role. Luna used low effort and Astra used medium effort for both implementation and review. Each call started a fresh thread. The routes ran sequentially in three rounds, rotating the order so each occupied every position once. There were twelve model calls, zero retries and zero rejected outputs.

The timer started before launching the implementer and stopped when the local acceptance check returned. It includes process startup, subscription checks, the review handoff where applicable, applying the settings and the test. Fixture preparation and the initial failure check were outside the timer. A Python driver acted as the controller, so these figures cover no separate conversation-model call.

Cost per accepted fix is the sum of the costs of all calls on a route divided by the number of accepted fixes. Across three attempts Luna used 57,945 input and 96 output tokens, Astra used 63,948 and 96, and the reviewed route used 122,736 and 401. No call reported cached input or cache-write tokens. We used Standard short-context API prices per million tokens: Luna $0.10 input, $0.01 cached input and $0.50 output; Astra $8.00, $0.40 and $40.00 respectively. Prices are an API equivalent for subscription traffic. [OpenAI pricing](https://developers.openai.com/api/docs/pricing)

For this small fix, the strong review added a second native context and found no change to make. Astra alone took 8.77 seconds on average and the reviewed route took 17.99 seconds. The task and sample are small. A [separate importer trial](model-cost-pilot.md) shows a case where review caught a rounding error.

## Reproduce the comparison

From the source checkout, recalculate these numbers offline:

```sh
python -I -B examples/csv-routes/summary.py
```

Inspect [per-call token counts, timing and submitted settings](../examples/csv-routes/receipts.json). To repeat the live trial using your own compatible subscription, follow the [native Codex setup](codex-stage.md), then run the [trial driver](../examples/csv-routes/run.py) with a new output directory. The command makes up to twelve model calls.

```sh
python -I -B examples/csv-routes/run.py --output my-csv-trial
```
