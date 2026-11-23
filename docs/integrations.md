# Optional integrations

## AgentWhisker

Install [AgentWhisker](https://github.com/StrangePers/agentwhisker) separately. In a local shell:

```sh
roleshuttle companion --state running
```

Choose the printed JSON file in AgentWhisker's status-file settings. Each invocation atomically updates the same file for this OS user. The optional projection uses one current task. It writes only a fixed state and generic title. A controller can update it from a trusted local compact task report:

```sh
roleshuttle companion --report <report.json>
roleshuttle companion --state idle
```

Valid states are idle, running, waiting and failed. Call after task transitions. The file holds its last update when the controller exits. Multiple controllers writing to it share the last update. Actual task text, repository paths, usage limits and credentials are outside this format.

## Project Fieldnotes

Follow the [public setup guide](https://github.com/StrangePers/project-fieldnotes#start-a-notebook-for-your-project) to choose a notebook in the appropriate project. The skill can consult its start page before asking a repeated question. When a human answer changes a lasting requirement, the controller can propose a decision with a reason, a source and a revisit condition. Link that decision from the start page. Explicitly select the notebook and what is safe to share. Keep operational receipts and sensitive evidence in their original private location.

The [fictional example](../examples/project-notes/START-HERE.md) contains an original decision, a source and a follow-up condition. No account connection, notebook upload or automatic export is involved.
