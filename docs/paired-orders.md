# How much work moves out of the chat?

On this Windows CPU trial, handing off a five-file lookup reduced the small chat model's input from 244 to 143 tokens across two attempts. Both routes gave the wrong team both times. The routed run also consumed 226 worker input tokens. This is a smaller chat context, with no demonstrated saving per accepted result.

## Try the same three tasks

Install the package from the source checkout and configure a local Ollama profile. The chat and researcher shared one model. The specialist and reviewer shared a second model with fresh message histories. Substitute your installed chat model tag for CHAT_MODEL_TAG in both commands.

```sh
python examples/compare_orders.py --profile local-roles.json --chat-model CHAT_MODEL_TAG --output run-a --max-output-tokens 384
python examples/compare_orders.py --profile local-roles.json --chat-model CHAT_MODEL_TAG --output run-b --max-output-tokens 384 --routed-first
```

Each attempt starts with the same synthetic Git revision in a new directory. Direct runs send the task to the selected chat model. Routed runs add a research call for the short edit or policy lookup, or a specialist and reviewer for export. The lookup controller receives only the worker's summary and the question. The export controller receives both assessments if both stages finish. An incomplete review stops the route.

The runner invokes models over the local API, restricts edits to option values or an answer file, and runs a fixed Python check. It supplies source excerpts and applies the final response mechanically. Native tool choice, tool prompts and human interaction are outside this comparison. Each arm gets one attempt per run, with zero retries. The scripts alternate the arm order, then reverse it on the second run. Ollama sampling and cache state can change results.

| Task and route | Passed | Chat tokens in / out | Worker tokens in / out | Wall seconds |
| --- | ---: | ---: | ---: | ---: |
| One delimiter edit, direct | 1/2 | 464 / 39 | 0 / 0 | 3.38 |
| One delimiter edit, research | 0/2 | 607 / 45 | 440 / 135 | 12.45 |
| Selected-order export, direct | 0/2 | 504 / 54 | 0 / 0 | 5.58 |
| Selected-order export, specialist + review | 0/2 | 0 / 0 | 1220 / 841 | 58.70 |
| Lookup across five policy files, direct | 0/2 | 244 / 6 | 0 / 0 | 2.19 |
| Lookup across five policy files, research | 0/2 | 143 / 5 | 226 / 24 | 5.56 |

Each row sums two runs, including failures. Times cover dispatch through the outcome and exclude fixture creation. Counters are Ollama's reported prompt evaluations and generated tokens. The two routed export reviews reached the 384-token limit before returning to the chat. The lookup models confused a policy revision with its owning team. Stronger reasoning, more context or a larger generation budget would be different experiments.

The local server was Ollama 0.40.17, CPU only, one loaded model at a time. Model identities are omitted, so these figures describe the recorded run only. The fixture commit was `9ebff7d853af924e03b36f661be517acdfea59c6`. The sample did not use a paid provider. Financial savings and changes in premium model time remain unmeasured.

## What happened in a native agent?

We also opened two fresh Codex CLI 0.166.0 sessions backed by the same local chat model, on the same fixture. One session was asked to fix the export directly. The other was asked to use the controller skill and configured specialist/reviewer route. The CLI reported fallback model metadata. Neither session emitted a shell call or changed the file, and the external acceptance check failed in both.

| Native chat | Reported input / cached / output tokens | Wall seconds | Export check |
| --- | ---: | ---: | --- |
| Direct request | 4302 / 0 / 204 | 71.92 | Failed |
| Request with RoleShuttle | 4365 / 3921 / 86 | 17.65 | Failed |

The native experiment covers one pair with different cache conditions. The second session never reached the worker route. Choose a chat model that can reliably call tools in your client. A model being able to answer a chat message does not demonstrate this capability.

## A completed handoff

In a separate run on the same fixture, a tool-capable Codex chat called `roleshuttle consult` with the existing settings, their meaning and the export requirements. Both local roles finished within a 1024-token limit each. The specialist used 137 input and 280 output tokens. The reviewer used 441 input and 433 output tokens. They agreed on selected scope, CSV encoding and preserving the delimiter. Their written advice also suggested conditional behavior outside the requested configuration change.

The main agent inspected the exporter and changed only two option values. The project check failed on the original revision and passed on the change, including empty selections and quoted multiline fields. The review fingerprint matched before and after the test. This demonstrates the complete handoff and correction in one chat. The main chat's token usage was unavailable for this run, so it is excluded from the paired results.
