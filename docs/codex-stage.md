# Direct Codex stages

Install RoleShuttle using the [README](../README.md). Install the official Codex CLI on the same machine (with npm available, run `npm install -g @openai/codex`). In the same OS account that will launch your coding agent, run `codex --version`, then `codex login` and `codex login status`. In a headless terminal, use `codex login --device-auth` and complete the sign-in in your browser. Confirm that the CLI reports a ChatGPT login. The adapter uses the local Codex app-server over stdio. CLI 0.166.0 was used for the first Windows trial. Compatibility depends on ephemeral threads, structured output, model and effort confirmation, and subscription quota reporting.

RoleShuttle looks for `codex` on PATH or `codex.exe` on Windows. If the Windows installation exposes an npm or PowerShell launcher, set `ROLESHUTTLE_CODEX_BIN` to the absolute path of the native executable in that installation. Keep credentials in the native client. Each stage checks that the client reports ordinary subscription usage, remaining quota and zero usable extra credits before requesting a turn. An unsupported or unavailable route returns an error. Each invocation has a three-minute deadline.

## Try the order export

From the RoleShuttle source directory, in the terminal with the virtual environment active, create a new example and reproduce the bug:

```sh
python examples/prepare_orders.py ../orders-codex
cd ../orders-codex
python -B checks.py export
roleshuttle skill --install opencode
```

The starting test raises an assertion. Launch OpenCode here with the inexpensive, tool-capable chat model you selected. If you use a different agent, install its project skill instead. The fixture creates a Git-ignored `.roleshuttle` directory for the original task and both receipts. This route uses your Codex subscription for the two workers.

Send this request in one chat:

> Use the roleshuttle skill and keep my current chat model. Fix the selected-orders CSV export. Use Codex Luna (`gpt-6-luna`, low effort) for the implementation proposal and Codex Astra (`gpt-6.2-astra`, medium effort) for independent review. Read the project requirements, source and initial settings. Put the original task and relevant excerpts in `.roleshuttle/task.txt`. Use the two commands below in order. Read Luna's receipt, then write the original requirements and Luna's actual proposal to `.roleshuttle/review.txt`. Ask Astra to check that proposal against the requirements with a concrete CSV edge case. If a stage fails, report the error and stop. If a worker needs clarification, ask me in this chat. After a successful review, check the advice against the source, apply the accepted settings only to `options.json`, and run `python -B checks.py export`. Report both confirmed models and the test result.
>
> `roleshuttle codex-stage --role implementer --model gpt-6-luna --effort low --input .roleshuttle/task.txt > .roleshuttle/luna.json`
>
> `roleshuttle codex-stage --role reviewer --model gpt-6.2-astra --effort medium --input .roleshuttle/review.txt > .roleshuttle/astra.json`

After the chat finishes, read both JSON receipts. Each should have status `returned` with the requested model and effort. In the example project, run `git diff --name-only` and `python -B checks.py export`. Expect only `options.json` and a passing check. Return to the RoleShuttle source directory and run `python -I -B examples/csv_holdout.py ../orders-codex` for the independent seven-case check.

Workers receive the supplied text in an empty temporary working directory with a read-only sandbox. The native client also supplies its own instructions. Use this command for text proposals, then apply and test the result in the main agent. Native usage can include substantial context overhead even for a short task. The Codex receipt reports native input tokens (including cached), cached input tokens and output tokens separately. Missing usage is returned as null. A failed or interrupted turn may already have consumed quota.
