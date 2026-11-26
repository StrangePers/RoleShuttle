# RoleShuttle

[Interactive walkthrough](https://strangepers.github.io/RoleShuttle/) · [Download the v0.1.0a14 preview](https://github.com/StrangePers/RoleShuttle/releases/tag/v0.1.0a14)

Choose the model you want to talk to. Choose who does the difficult work.

You can use a lightweight model for everyday conversation. When an export bug needs a design decision, your main agent can send the relevant code to the specialist you chose, get a second opinion from your reviewer, then apply and test the change. If the requirements are ambiguous, the question comes back to you in the same chat. You can also keep a strong conversation model and delegate routine investigation to smaller workers.

## A verified handoff from a lightweight chat

In a clean Ubuntu 24.04 environment, a user asked a free OpenCode chat model (`opencode/deepseek-v4.1-flash-free`) to fix a CSV export that included unselected orders and mishandled quoted names. The chat used RoleShuttle to call two workers through a Codex subscription.

| Stage | Model | What happened |
| --- | --- | --- |
| Implementation proposal · 9.06 s | `gpt-6-luna` · low | Luna selected `scope=selected` and `encoding=csv`, keeping the existing semicolon delimiter. |
| Independent review · 14.09 s | `gpt-6.2-astra` · medium | Astra received the requirements and Luna’s full reply. It checked row order, empty selections and CSV escaping, then accepted the proposal. |

The OpenCode chat applied the two settings to `options.json` and ran the project test. The chat took **75 seconds** from its first event to its last. After it finished, a separate check passed **7/7 CSV cases**, including a customer name containing a line break. The timings describe one run and exclude setup and the independent check. [Reproduce the handoff in your own chat](docs/codex-stage.md#try-the-order-export).

## Start in your own agent

Download and unpack the source, then open a terminal in the directory containing `pyproject.toml`. Install Python 3.11 or newer and Git. On a minimal Ubuntu 24.04 installation, run `sudo apt-get update` and `sudo apt-get install python3-venv git` (as root, omit `sudo`).

On Linux or macOS, create and activate a virtual environment in that directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
roleshuttle preset codex --role reviewer
```

On Windows, in PowerShell:

```powershell
py -3 -m venv .venv
$env:Path = (Resolve-Path .venv/Scripts).Path + ";" + $env:Path
python -m pip install .
roleshuttle preset codex --role reviewer
```

The command should print the reviewer model and its effort. Keep this terminal open for the next steps. For a first verified task, choose [a lightweight chat with Codex Luna implementing and Astra reviewing](docs/codex-stage.md#try-the-order-export) or [two local Ollama roles](README.md#try-one-request-in-your-agent). To explore the walkthrough, run `roleshuttle demo --open` on a desktop or `roleshuttle demo` to print the HTML file path.

Then change to the root of **the project you want to work on**. Run one command for the agent you use there.

| Agent | Install the controller skill |
| --- | --- |
| Codex | `roleshuttle skill --install codex` |
| Claude Code | `roleshuttle skill --install claude-code` |
| OpenCode | `roleshuttle skill --install opencode` |

The command prints the installed file path. Open the project in your agent with the RoleShuttle CLI available on its PATH. For a first check that starts no worker, ask:

> Use the roleshuttle skill. Read the Codex preset for implementer and reviewer and tell me the model and effort for each. Stop before dispatch.

The packaged preset returns Sol 6.2 at medium effort for implementation and Astra 6.2 at medium effort for review. Once you are ready to send a task, follow the [direct Codex handoff](docs/codex-stage.md) or the [local Ollama example](README.md#try-one-request-in-your-agent). The skill host and worker provider are independent choices.

Install again to check the existing copy. If you have customized it, compare it with the output of `roleshuttle skill` and choose which instructions to keep. Codex and Claude Code discovered the project skill on Windows. OpenCode 1.19.6 listed it on Ubuntu. On Windows, OpenCode 2.3.0 installed the project layout but returned an empty standalone skill list. If your client does not list RoleShuttle, ask it to run `roleshuttle skill` and use the printed instructions for the current task. [OpenCode skill discovery](https://opencode.ai/docs/skills/)

## Choose workers for your chat

The conversation model stays under your control. Give the main agent a preset name and ask it to look up the role for each stage. For example, the Codex preset assigns implementation to Sol at medium effort and independent review to Astra at medium effort.

```sh
roleshuttle preset
roleshuttle preset codex --role reviewer
roleshuttle preset opencode-go --lane critical
roleshuttle preset openrouter-free
```

| Preset | What it contains |
| --- | --- |
| Codex | Six role assignments with reasoning effort, from assistant to reviewer |
| OpenCode Go | Six role assignments plus separate architecture and critical-task choices |
| OpenRouter Free | An ordered preference for tool-capable free routes, with no paid fallback |

The commands return JSON offline. To use a choice for live work, your agent needs an authorized executor that supports that model. Run `roleshuttle models serve` to edit assignments in your private profile. [Cloud preset guide](docs/cloud-presets.md) · [Local model settings](docs/integrations.md#standalone-model-settings)

Using a different subscription? A fast, tool-capable model such as Claude Haiku or Gemini Flash can handle conversation and bounded lookups. For a consequential design decision, consider Claude Opus or Gemini Pro. Open **Which model fits each role?** in `roleshuttle demo --open` for all six roles, Claude, Gemini, Codex, Kimi and Qwen examples, and a check for each assignment. [Guide for other subscriptions](docs/cloud-presets.md#choose-equivalents-on-another-subscription) · [Worked example](examples/choose-workers.md)

## Reproduce the examples

The offline walkthrough follows one fictional order-export request from the chat through the workers and back. To check the figures on the project page yourself, run these commands from the source checkout. They use recorded token counts and an actual submitted patch; they require no model account.

```sh
python -I -B examples/csv-routes/summary.py
python -I -B examples/reconciliation/price_pilot.py
python -I -B examples/reconciliation/gate.py examples/reconciliation/submitted/luna-reviewed
```

The CSV summary prints the three recorded routes. The importer check should report 21 passed groups. For live delegation, start with [a request in your own agent](README.md#try-one-request-in-your-agent). Local model calls use Ollama. The three-route CSV trial runs directly through Codex. Repeating the earlier worktree-based importer trial requires a compatible task runtime.

The local review command inspects a trusted worktree against a full commit ID and file scope:

```sh
roleshuttle review --repo . --base <full-commit-id> --allow src/ --task-id example
```

It returns a fingerprint, a bounded diff and scope violations. Exit code 0 means the snapshot is reviewable. Passing project checks and accepting a change require separate evidence. Inspect code only in repositories you trust.

## Live delegation

### Hand a stage to Codex

Keep your conversation in your usual tool-capable agent. Install a compatible native Codex CLI and sign in with ChatGPT. The public package can send a text stage directly to Codex. For example, paste this request into your agent with a small export fixture open:

> Keep my current chat model. Ask the RoleShuttle Codex implementer for the corrected export settings. Pass the requirements and its proposal to a fresh reviewer. Ask me here if either worker needs clarification. Apply the accepted settings and run the export tests.

The agent can run these commands from any directory. Supply approved excerpts on stdin or in a UTF-8 file. Build the review input from the original requirements and the actual proposal.

```sh
roleshuttle codex-stage --role implementer --input task.txt
roleshuttle codex-stage --role reviewer --input review.txt
```

Each command makes one native turn. A successful receipt includes the model and effort confirmed at thread start, text, status, and token counts when available. For a small edit you can select a different worker, for example `--role implementer --model gpt-6-luna --effort low`. The main agent owns edits and acceptance. [Setup and a worked handoff](docs/codex-stage.md)

### Use local model roles from your chat

Your client selects the conversation model. The RoleShuttle profile selects the workers. The main model must reliably call shell tools in your client and read their results. It decides when a task calls for a specialist. You decide which models may fill each role.

Run a trusted [Ollama](https://docs.ollama.com/api/chat) server on your own machine with local models installed. Set the server to [local-only mode](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features). Copy [the role profile](examples/local-roles.json) to your own workspace and set each role to the exact installed model tag you want. On the first stage the controller can run:

```sh
echo "Which edge cases matter when writing selected orders to CSV?" | roleshuttle delegate --profile local-roles.json --role research --max-output-tokens 128 --input -
```

RoleShuttle sends the task text and role instruction to the loopback server. The receipt includes the model, response, stop reason and available input/output token counts. Ollama reports total, loading, prompt evaluation and generation durations in nanoseconds. Missing counters are null. Each call accepts an output budget of 1–2048 tokens and defaults to 2048. The main agent chooses the handoff, checks the result and asks any human question. The text worker receives no tools or filesystem access.

Exit 0 means Ollama finished generating the response. Validate the answer against the task criteria before accepting it. Exit 1 preserves a partial receipt when generation hits a limit or returns another non-stop reason. [What happened in a real two-model run](docs/local-run.md)

For a decision and a review on local models, the main agent can call `roleshuttle consult --profile local-roles.json --input stage.txt`. The command returns the specialist's proposal and the reviewer's assessment in one JSON response. Each role uses its own context and its configured model tag. The main agent checks the evidence and owns the final response.

In the orders example, a Codex chat used this route, applied the two settings the source code supported, and verified quoted names, multiline fields and empty selections. [See the completed handoff](docs/paired-orders.md#a-completed-handoff)

### Try one request in your agent

You need a shell-capable coding agent and a running local [Ollama installation](https://ollama.com/download). The example uses one small public model for both roles in separate contexts. You can assign a different installed tag to each role later. The model you talk to in your coding agent is a separate choice.

1. Start Ollama in local-only mode. For a fresh install with no server running, open a second terminal and run `OLLAMA_NO_CLOUD=1 ollama serve` (PowerShell: set `$env:OLLAMA_NO_CLOUD = "1"`, then run `ollama serve`). If Ollama is already running as a service or desktop app, [configure the existing server](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features) and restart it. In your original terminal run `ollama pull qwen2.5-coder:1.5b` and confirm the tag appears in `ollama list`. The first download is about 1 GB.
2. From the RoleShuttle source directory, create a fresh example project with the public demo profile:

   ```sh
   python examples/prepare_orders.py ../orders-demo --with-local-profile
   ```

3. Run `cd ../orders-demo` and `python -B checks.py export`. An assertion failure here reproduces the bug. Install the skill **in this directory**, for example with `roleshuttle skill --install opencode` (use `codex` or `claude-code` for those clients). Launch your coding agent here from the same terminal so it inherits the CLI on PATH. For a GUI launched separately, run `python -c "import shutil; print(shutil.which('roleshuttle'))"` in this terminal and give the agent that executable path.
4. Send this request in the project chat. The profile and stage files stay inside a Git-ignored project directory the agent can access with ordinary workspace permissions.

   > Use the roleshuttle skill. Fix the export of selected orders. Read the requirements, source and starting options. Put the task and relevant excerpts in `.roleshuttle/stage.txt`. Run `roleshuttle consult --profile .roleshuttle/roles.json --input .roleshuttle/stage.txt > .roleshuttle/receipt.json`. Read both responses and check them against the source. Apply the fix only to options.json and run `python -B checks.py export`. Report the roles, models and test result.

5. From `orders-demo`, run the project check again and inspect the tracked diff:

   ```sh
   python -B checks.py export
   git diff --name-only
   ```

   Expect `passed` and only `options.json` in the tracked diff. The installed project skill is a separate, untracked file. Return to the RoleShuttle source directory and run `python -I -B examples/csv_holdout.py ../orders-demo`. Expect `7/7 held-out cases passed`.

### Use an existing guarded runtime

The optional bridge connects to a separately configured task runtime. Worktree-writing workers, wait, recorded decisions, project checks and acceptance use that runtime's own policies and verified adapters. A standalone public runtime for writing tasks and the configuration UI are release work in progress.

To reserve a paid model for architecture decisions or review, configure that role, provider and spending permissions in your compatible runtime. The standalone `delegate` and `consult` commands use locally installed Ollama models.

Configure the absolute runtime executable and any fixed arguments in your [private profile](docs/integrations.md#private-runtime-configuration). The `ROLESHUTTLE_RUNTIME_ARGV` JSON array is also supported. Invoke `roleshuttle task show --task-id <id> --collect` to read a report, or run `roleshuttle dashboard` to open the local panel if your runtime supplies one. Running `roleshuttle skill` prints the controller instructions. The project skill installation is opt-in.

The [example task contract](examples/export-stage.json) illustrates one bounded implementation stage. To use it with a compatible runtime, first register your own trusted project and its check, then substitute their IDs and allowed paths. The example disables extra usage.

## Related projects

- [AgentWhisker](https://github.com/StrangePers/agentwhisker) is the optional desktop companion. RoleShuttle writes its documented JSON status format so you can follow a task from your desktop. [Connect the companion](docs/integrations.md#agentwhisker)
- [Project Fieldnotes](https://github.com/StrangePers/project-fieldnotes) supplies a Markdown notebook for decisions worth keeping between chats. The controller can turn a reviewed human decision into a note in the selected project. [Follow a fictional decision and its evidence](examples/project-notes/START-HERE.md)
- A future execution backend may supply nodes and resource admission. Its public name, availability and integration contract are pending.

## Measuring the benefit

On the same two-setting CSV fix, Luna alone averaged **$0.001948 and 7.93 s**, Astra alone **$0.171808 and 8.77 s**, and Luna with an independent Astra review **$0.178790 and 17.99 s** per accepted result. Each route passed all seven checks in three out of three attempts. Costs are Standard API equivalents for measured subscription tokens. [Compare all three routes and reproduce the figures](docs/three-route-csv.md)

On a separate two-supplier importer, Astra’s review caught a precision error after Luna passed the original checks. The corrected route came to 3.8% below Astra alone in that trial. [Inspect the correction and its cost](docs/model-cost-pilot.md)

On a two-supplier import fix, Astra spent 92 seconds implementing the change alone and 45 seconds reviewing Sol's patch. Both passed the same 21 held-out test groups. The routed run used more tokens overall. [See the Codex subscription case study and rerun the checks](docs/subscription-pilot.md)

In a local five-file lookup, delegation reduced chat input from 244 to 143 tokens across two attempts, added 226 worker input tokens, and produced zero correct answers in either route. On a one-option edit the direct model passed once in two attempts and finished sooner. Choose routing per task and measure time and cost per accepted result. [Reproduce the paired comparison and see the native chat trial](docs/paired-orders.md)

## License

RoleShuttle is licensed under the [MIT License](LICENSE). Copyright © 2026 StrangePers.
