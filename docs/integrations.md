# Optional integrations

## AgentWhisker

Install [AgentWhisker](https://github.com/StrangePers/agentwhisker) separately. In a local shell:

```sh
roleshuttle companion --state running
```

Choose the printed JSON file in AgentWhisker's status-file settings. Each invocation atomically updates the same file for the selected profile. The manual state command writes a fixed state and generic title. A controller can update the file from a trusted local compact task report:

```sh
roleshuttle companion --report <report.json>
roleshuttle companion --state idle
```

Valid states are idle, running, waiting and failed. Call after task transitions. The file holds its last update when the controller exits. Multiple controllers writing to it share the last update. Actual task text, repository paths, usage limits and credentials are outside this format.

For a task runtime with the Agent Console report format, publish a complete snapshot from the registered task IDs:

```sh
roleshuttle companion --task-id <first-id> --task-id <second-id>
```

You can also supply one `--report` per task instead. Both modes preserve the runtime's IDs and the status of each task. Wait states appear first. RoleShuttle replaces the file only after it has read and validated the complete snapshot. Run one publisher for the set of tasks you want to show. The generic JSON source can display these rows. Opening a runtime card requires the corresponding trusted AgentWhisker integration.

To track standalone runs, add `--record` to `delegate`, `consult` or `codex-stage`. The returned `stage_id` also identifies the on-disk result. Use `roleshuttle stage <stage-id>` to read it after a restart. A recorded result can contain sensitive text. Store the profile outside your repositories and keep its access local.

```sh
roleshuttle companion --snapshot
roleshuttle companion --follow --runtime-status <absolute-path-to-console-bridge-status.json>
```

The first command prints the output path. Point AgentWhisker at that file. The second refreshes it every two seconds from recorded stages and, if supplied, the existing Console bridge. Run it in one local process. It closes on Ctrl+C. Stale Console samples stop contributing task rows. The output contains task IDs, source, generic titles, states and timestamps. Results and decision text remain in the originating profile. Standalone stages use their own IDs. Use the `stage` command to open those results.

With a compatible AgentWhisker build, select the **RoleShuttle** source and click a finished stage to open its recorded result. The default source is `~/.roleshuttle/agentwhisker-status.json`. For a separate profile, set `AGENTWHISKER_ROLESHUTTLE_STATUS_FILE` to the absolute snapshot path before launching the pet. Console rows continue to use the pet's configured Console launcher to open their task cards.

## Private runtime configuration

The default profile directory is `~/.roleshuttle`. Set `ROLESHUTTLE_HOME` to an absolute directory to isolate a candidate build. In that directory, store `runtime.json` as a JSON object with `schema_version` set to `1` and `argv` set to the trusted runtime executable and any fixed arguments. Keep that file outside project repositories. `ROLESHUTTLE_RUNTIME_ARGV` overrides the file when set. Configure the runtime's own data directory separately when running on test data.

If the configured runtime supports the Agent Console `serve` command, run `roleshuttle dashboard` to open its existing model settings and task console. Run `roleshuttle dashboard --task-id <id>` to open a specific task. Use `--port` to select a separate test listener. The dashboard is supplied by the configured runtime and uses its local profile and authentication.

## Standalone model settings

Run `roleshuttle models serve` to open the local RoleShuttle panel. The panel saves role and category assignments, including Codex effort, in a versioned database under the private profile directory. Load a bundled preset as a draft or edit the fields, then save. If another window changed the settings, reload before saving. Use the **Reload saved settings** button to keep the current panel session.

`codex-stage --use-profile` uses a saved Codex role subject to the same native checks as an explicit model selection. The existing invocation keeps using its bundled defaults and explicit flags. Go assignments and category overrides can be saved for migration. Guarded tasks sent to Agent Console continue using their runtime's own routing policy.

```sh
roleshuttle models preview-console <path-to-console-config>
roleshuttle models import-console <path-to-console-config> --source-revision <console-revision> --target-revision <target-revision>
```

Review the differences and warnings before applying. The import merges supported assignments into the RoleShuttle profile. Existing local assignments absent from the source are preserved. Ordered lists with multiple choices and unsupported providers are reported in the preview. The source preset is reported for reference. Its rules, including how Agent Console computes `recommended`, remain in the Console runtime.

## Offline Recommended preview

```sh
roleshuttle recommend <snapshot.json>
```

Provide a JSON object with the following fields:

| Field | Contents |
| --- | --- |
| `task` | First-attempt task classification, required tools, context estimate, data and Extra Usage permissions, optional role, priority and dependencies |
| `policy` | Materialized r28-compatible routing policy with routes, current lane lists, model enrollment, freshness and admission rules |
| `baseline_routing` | Default lane lists for the corresponding policy version, used to detect explicitly narrowed lanes |
| `availability` | Saved route, subscription and optional free-catalog observations |
| `now` | The comparison time as Unix seconds. Observations carry their original timestamps |

The JSON output includes the chosen route, alternatives, quota pressure and decision reasons. `execution_admissible` mirrors the decision **in the supplied snapshot**. The command runs in `plan-only` mode and grants no authority to launch. Use synthetic snapshots for shared examples. Operational snapshots can describe private infrastructure and should stay local. Existing tasks with prior attempts still use the runtime's own retry and reconciliation logic.

The same calculation is available under **Recommended preview** in `roleshuttle models serve`. Select the snapshot JSON file and click **Preview route**. The authenticated loopback panel processes the snapshot in memory. It shows the selected model and effort, the decision based on the recorded checks, and expandable reasons for each candidate. Previewing leaves the saved assignments intact. Project authorization must already be reflected in the snapshot, as it is after r28 has prepared a task.

## Migrating private projects from Agent Console r28

Run the preview and import against a private profile copy outside the repository.
The source is a directory containing the r28 `projects/` files. The destination
is selected by `ROLESHUTTLE_HOME`.

```sh
roleshuttle local-project preview-console <r28-copy>
roleshuttle local-project import-console <r28-copy> --source-revision <source> --target-revision <target>
roleshuttle local-project resolve <checkout>
roleshuttle local-project audit
roleshuttle recommend <snapshot.json> --project <project-id>
```

The import disables both Extra Usage flags in the destination and preserves the
saved executable hashes. It imports profiles as a single registry update, rejects
conflicting local registrations and leaves an identical retry unchanged.
The report lists checkout and source binding issues separately from check
executables whose saved hashes no longer match. The audit reads these files
without running the registered commands. `ready` counts valid checkouts.
`ready_for_checks` also requires registered checks and matching executable hashes. After verifying a replacement tool,
update its registration with the current project revision. Model assignments use the separate `models import-console` flow.

A registered source must opt in to adding linked worktrees. RoleShuttle checks
Git's worktree registry, the shared Git directory and the source profile
revision. After a source change, resolve an authorized linked checkout again.
Any existing task retains its original project revision.

Project-scoped Recommended previews apply project cloud permissions, the data
boundary and exact local capability evidence. They read observations and return
a plan without contacting a provider. Model availability and quota still depend
on the observations supplied. Paths to installed tools, checkouts and operator
evidence refer to files on the local machine.

## Running baseline checks from the panel

Install the RoleShuttle wheel with `python -m pip install <wheel-file>`.
Use an absolute `ROLESHUTTLE_HOME` directory to start with a separate private
profile. The default profile is `~/.roleshuttle`.

Save a project configuration outside the repository. Replace the example paths
with the absolute paths to a Git checkout and a Python environment with pytest
installed.

```json
{
  "id": "example",
  "repository": "/absolute/path/to/project",
  "allowed_paths": ["tests/"],
  "checks": [
    {"id": "python-unit", "argv": ["/absolute/path/to/python", "-B", "-m", "pytest", "-q", "tests"]}
  ]
}
```

```sh
roleshuttle local-project register /absolute/path/to/project-profile.json
roleshuttle models serve --local-tasks
```

In the loopback panel, select the project, then choose **Prepare baseline
checks** and **Run baseline checks**. Preparation pins the project's current
commit. RoleShuttle creates a clean, detached worktree there and runs the
registered checks locally with Extra Usage disabled. For a failing check,
**Open failed check log** opens the last 32 KiB of stdout and stderr. Project
profiles, task receipts and check logs stay in the private profile.

## Preparing local tasks

```sh
roleshuttle local-project register <project.json>
roleshuttle local-task prepare <task.json> --request-id example-request
roleshuttle local-task show <task-id>
```

The project file has an `id`, an absolute path to the Git worktree root in `repository`, and an `allowed_paths` list. Use a trailing slash for a directory. Optional boolean fields `external_code_allowed`, `shared_mac_allowed`, and `extra_usage_allowed` default to false. Use `local-project show <id>` to read the current revision and pass it as `--revision` when updating a registration.

The task file has `project_id`, `title`, `mode` (`read` or `write`), and an `allowed_paths` list within the project scope. Optional `allow_extra_usage` defaults to false and requires project permission. Preparation pins the full base commit and project revision and stores the task under a stable ID. Repeating a preparation with the same request ID and contract returns the original record. The private RoleShuttle profile must be outside the registered repository. Prepared local records can be inspected across process restarts.

### Creating and recovering the worktree

```sh
roleshuttle local-task worktree <task-id>
roleshuttle local-task show <task-id>
```

RoleShuttle checks the project revision and source HEAD before creating one detached worktree in the private profile. The worktree starts from the full pinned commit. Uncommitted changes in the source tree stay there. The `worktree_ready` record gives the destination. A repeated call returns the same record.

After an interruption, the task records `creating_worktree`. Confirm that the interrupted Git process has stopped, then run:

```sh
roleshuttle local-task reconcile-worktree <task-id> --confirm-git-stopped
```

A clean worktree registered to the right repository and pinned commit is attached to the same task. If neither the path nor a Git worktree registration exists, the task returns to `prepared` and creation can be retried. A dirty, partial or inconsistent tree stays in place and requires inspection.

## Proposing a patch with a native subscription model

Supply a recent Recommended snapshot for the pinned project and a text file
containing the operator-approved instructions and excerpts. The first
standalone route supports a low-risk Codex implementer with Extra Usage off.
The native client checks ordinary subscription quota and zero usable extra
credits before starting a turn. The model receives the approved text in an
ephemeral read-only stage and returns a patch proposal.

```sh
roleshuttle local-task propose-patch <task-id> --prompt <approved.txt> --snapshot <routing.json>
roleshuttle local-task apply-model-patch <task-id>
```

The model operation is recorded before the request. Repeating the same prompt
after a saved result returns that result. A request with an uncertain outcome
remains in the task for inspection without an automatic retry. Apply uses the
existing scoped patch executor. Git checks the proposal against the clean pinned
base, including proposals without context lines. Review, check, and accept use its saved
fingerprint. A model question is saved with the task.

To answer a saved model question, read its ID from the private task record.
Use the original approved prompt, an answer file, and a fresh snapshot that
still admits the pinned model and effort.

```sh
roleshuttle local-task answer-model-question <task-id> --question-id <question-id> --prompt <approved.txt> --answer <answer.txt> --snapshot <routing.json>
```

Each answer starts a new ephemeral stage under the same RoleShuttle task.
It carries the approved prompt and the saved clarifications. A repeated answer
only reads the saved result. The question history stays in the private profile.

To send an unaccepted model result for correction, use its saved review
fingerprint and a file containing the operator's comment.

```sh
roleshuttle local-task revise-model-patch <task-id> --fingerprint <reviewed-fingerprint> --prompt <approved.txt> --comment <comment.txt> --snapshot <routing.json>
roleshuttle local-task apply-model-patch <task-id>
```

The correction is an incremental patch against the reviewed worktree, with a
new attempt under the same task ID. The existing check receipt is kept with
the earlier attempt. Review and run checks against the new fingerprint before
acceptance.

To stop a running model operation, read its ID from the private task record
after an active provider response has been observed.

```sh
roleshuttle local-task cancel-model <task-id> --operation-id <model-operation-id>
roleshuttle local-task show <task-id>
```

The cancellation request is saved once. RoleShuttle waits for the native turn
to terminate as interrupted and confirms that the provider closed the specific
open response. The final task record contains this receipt. If confirmation is
missing, the task requires reconciliation before it can proceed.

## Applying and reviewing a local patch

```sh
roleshuttle local-task execute <task-id> --patch <changes.patch>
roleshuttle local-task review <task-id>
```

The local executor applies an operator-supplied UTF-8 text patch of up to 2 MiB. It requires a writable task, the current project revision, and a clean pinned worktree. Run it on trusted repositories. The executor checks the patch paths and saves the attempt before Git writes. It makes no commit and adds nothing to the index. Binary patches, renames, copies and mode changes are outside this executor's supported format.

The saved receipt has a patch digest, changed paths and a review fingerprint. A repeated invocation with the same patch returns the original receipt. `local-task review` reads the current worktree and reports whether its fingerprint still matches.

If execution stops in `applying_patch`, inspect the existing diff and confirm Git has stopped. Then run `local-task reconcile-execution <task-id> --fingerprint <reviewed-fingerprint> --confirm-git-stopped`. This records the operator-reviewed result if its paths match the recorded attempt. A verified clean tree can return to `worktree_ready`, with the interrupted attempt retained. Reconciliation never reapplies the patch.

### Running checks and accepting a local result

Before preparing a task, register the project's trusted checks in its profile as a `checks` array. Each item has an `id`, an `argv` array starting with an absolute executable path and, optionally, a `timeout` from 1 to 300 seconds. Register updates using the current project revision.

```sh
roleshuttle local-task review <task-id>
roleshuttle local-task check <task-id> --fingerprint <reviewed-fingerprint>
roleshuttle local-task accept <task-id> --fingerprint <reviewed-fingerprint>
```

Review the diff before authorizing check commands, including any scripts they execute in the worktree. Checks run locally with a time limit and process-tree cleanup. Their receipt records exit codes and is bound to the exact attempt, project revision and fingerprint. Acceptance requires all registered checks to pass and the worktree to match the reviewed result. Acceptance records a local decision and leaves integration or committing to the operator.

Profiles using `acceptance_mode: project` also require
`local-task accept <task-id> --fingerprint <fingerprint> --project-proof <proof.json>`.
The proof contains `task_id`, the checked task `revision`, the `attempt` ID,
`fingerprint` and ordered `checks`. Each check has the exact registered
requirement as `id`, `status: passed`, an absolute `evidence` file outside the
task worktree, and its `evidence_sha256`. RoleShuttle validates the evidence
bytes and the passing local-check receipt for that result. Registered
`review_paths` are included in the review fingerprint.

If the checker stops before recording a receipt, confirm its processes have ended and use `local-task reconcile-checks <task-id> --fingerprint <reviewed-fingerprint> --confirm-checks-stopped`. A matching worktree can run the checks again. Model-backed workers continue to use the configured `roleshuttle task` runtime.

## Project Fieldnotes

Follow the [public setup guide](https://github.com/StrangePers/project-fieldnotes#start-a-notebook-for-your-project) to choose a notebook in the appropriate project. The skill can consult its start page before asking a repeated question. When a human answer changes a lasting requirement, the controller can propose a decision with a reason, a source and a revisit condition. Link that decision from the start page. Explicitly select the notebook and what is safe to share. Keep operational receipts and sensitive evidence in their original private location.

The [fictional example](../examples/project-notes/START-HERE.md) contains an original decision, a source and a follow-up condition. No account connection, notebook upload or automatic export is involved.
