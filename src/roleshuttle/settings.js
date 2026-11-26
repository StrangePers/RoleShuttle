"use strict";

const navigation = new URLSearchParams(location.hash.slice(1));
const token = navigation.get("token");
const archiveTarget = navigation.get("archive");
let taskTarget = navigation.get("task");
if (token) history.replaceState(null, "", location.pathname);
const status = document.querySelector("#status");
const save = document.querySelector("#save");
const preset = document.querySelector("#preset");
let current;
let rows = [];

async function request(body, path = "/api/settings") {
    const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: {"Authorization": "Bearer " + (token || ""), "Content-Type": "application/json"},
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error);
    return value;
}

function select(values) {
    const field = document.createElement("select");
    for (const [value, label] of values) field.add(new Option(label, value));
    return field;
}

function labeled(text, input) {
    const label = document.createElement("label");
    label.append(text, input);
    return label;
}

function render(settings) {
    rows = [];
    for (const group of ["roles", "lanes"]) {
        const container = document.getElementById(group);
        container.replaceChildren();
        for (const name of current[group]) {
            const assignment = settings[group][name];
            const row = document.createElement("div");
            row.className = "row";
            const heading = document.createElement("strong");
            heading.textContent = name[0].toUpperCase() + name.slice(1);
            const provider = select([["", "Default"], ...current.providers.map(value =>
                [value, value === "codex" ? "Codex" : "OpenCode Go"])]);
            const model = document.createElement("input");
            model.maxLength = 160;
            const effort = select(current.efforts.map(value => [value, value]));
            provider.value = assignment?.provider || "";
            model.value = assignment?.model_id || "";
            effort.value = assignment?.effort || "medium";
            const sync = () => {
                model.disabled = !provider.value;
                model.required = Boolean(provider.value);
                effort.disabled = provider.value !== "codex";
            };
            provider.addEventListener("change", sync);
            sync();
            provider.setAttribute("aria-label", name + " provider");
            model.setAttribute("aria-label", name + " model ID");
            effort.setAttribute("aria-label", name + " effort");
            row.append(heading, labeled("Provider", provider), labeled("Model ID", model), labeled("Effort", effort));
            container.append(row);
            rows.push({group, name, provider, model, effort});
        }
    }
}

async function load() {
    save.disabled = true;
    try {
        current = await request();
        preset.replaceChildren();
        for (const name of Object.keys(current.presets)) preset.add(new Option(name, name));
        render(current.settings);
        status.textContent = "Saved settings loaded.";
        save.disabled = false;
    } catch (error) {
        status.textContent = error.message;
    }
}

document.querySelector("#reload").addEventListener("click", load);
document.querySelector("#load-preset").addEventListener("click", () => {
    if (!current) return;
    render(current.presets[preset.value]);
    status.textContent = "Preset loaded into the draft. Save to apply it.";
});
document.querySelector("#settings").addEventListener("submit", async event => {
    event.preventDefault();
    if (!current || save.disabled) return;
    const settings = {roles: {}, lanes: {}};
    for (const row of rows) {
        if (!row.provider.value) continue;
        const assignment = {provider: row.provider.value, model_id: row.model.value.trim()};
        if (assignment.provider === "codex") assignment.effort = row.effort.value;
        settings[row.group][row.name] = assignment;
    }
    save.disabled = true;
    try {
        Object.assign(current, await request({revision: current.revision, settings}));
        try {
            await loadLocalCatalog();
            status.textContent = "Settings saved.";
        } catch {
            status.textContent = "Settings saved. Reopen the panel before preparing a task.";
        }
    } catch (error) {
        status.textContent = error.message;
    } finally {
        save.disabled = false;
    }
});

const routing = document.querySelector("#routing");
const snapshotFile = document.querySelector("#snapshot");
const routingStatus = document.querySelector("#routing-status");
const routingResult = document.querySelector("#routing-result");
const previewButton = document.querySelector("#preview");

function appendText(parent, tag, text) {
    const element = document.createElement(tag);
    element.textContent = text;
    parent.append(element);
    return element;
}

function renderPreview(decision) {
    const selected = decision.selected_route;
    routingStatus.textContent = !selected ? "No eligible route in the snapshot."
        : decision.execution_admissible ? "Route passed the recorded admission checks."
        : "Route identified. Admission checks did not pass.";
    if (selected) {
        appendText(routingResult, "h3", decision.assigned_model);
        appendText(routingResult, "p", [selected, decision.assigned_provider, decision.effort].filter(Boolean).join(" · "));
    }
    for (const reason of decision.reason || []) appendText(routingResult, "p", reason);
    if (decision.recommended) {
        appendText(routingResult, "h3", "Subscription pressure");
        const pools = appendText(routingResult, "ul", "");
        for (const [name, pool] of Object.entries(decision.recommended.pools)) {
            appendText(pools, "li", pool.known
                ? `${name} · ${pool.used_percent}% used · pressure ${pool.pressure}`
                : `${name} · ${pool.reason}`);
        }
    }
    if (decision.saved_candidates?.length) {
        appendText(routingResult, "h3", "Saved assignment and fallbacks");
        const list = appendText(routingResult, "ol", "");
        for (const candidate of decision.saved_candidates) {
            appendText(list, "li", [candidate.provider,candidate.model_id,candidate.effort].filter(Boolean).join(" · "));
        }
    }
    if (!decision.alternatives.length) return;
    appendText(routingResult, "h3", "Routes considered");
    for (const row of decision.alternatives) {
        const entry = appendText(routingResult, "details", "");
        const verdict = row.execution_admissible ? "Passes snapshot checks"
            : row.eligible ? "Plan only" : "Ineligible";
        appendText(entry, "summary", `${row.route} · ${row.model} · ${verdict}`);
        if (row.reasons.length) {
            const reasons = appendText(entry, "ul", "");
            for (const reason of row.reasons) appendText(reasons, "li", reason);
        }
    }
}

routing.addEventListener("submit", async event => {
    event.preventDefault();
    if (previewButton.disabled) return;
    const file = snapshotFile.files[0];
    routingResult.replaceChildren();
    if (!file || file.size > 1_048_576) {
        routingStatus.textContent = "Choose a JSON snapshot of up to 1 MiB.";
        return;
    }
    previewButton.disabled = true;
    snapshotFile.disabled = true;
    routingStatus.textContent = "Evaluating snapshot…";
    try {
        renderPreview(await request(JSON.parse(await file.text()),
            document.querySelector("#preview-advanced").checked ? "/api/advanced-preview" : "/api/recommended"));
    } catch (error) {
        routingStatus.textContent = error instanceof SyntaxError ? "The snapshot is not valid JSON." : error.message;
    } finally {
        snapshotFile.disabled = false;
        previewButton.disabled = false;
    }
});
const panel = document.querySelector("#console-tasks");
const taskForm = document.querySelector("#console-form");
const projectSelect = document.querySelector("#console-project");
const taskStatus = document.querySelector("#console-status");
const prepareButton = document.querySelector("#console-prepare");
const launchButton = document.querySelector("#console-launch");
const cancelButton = document.querySelector("#console-cancel");
const cancelDialog = document.querySelector("#console-cancel-dialog");
const cancelTaskLabel = document.querySelector("#console-cancel-task");
const cancelScope = document.querySelector("#console-cancel-scope");
const reconcileForm = document.querySelector("#console-reconcile");
const localStopped = document.querySelector("#console-local-stopped");
const providerStopped = document.querySelector("#console-provider-stopped");
const reconcileReason = document.querySelector("#console-reconcile-reason");
const reconcileButton = document.querySelector("#console-submit-reconcile");
const historyLink = document.querySelector("#console-history");
const projectRefresh = document.querySelector("#console-refresh");
const resumeForm = document.querySelector("#console-resume");
const taskIdField = document.querySelector("#console-task-id");
const reviewSection = document.querySelector("#console-review");
const diffButton = document.querySelector("#console-show-diff");
const checkButton = document.querySelector("#console-check");
const acceptButton = document.querySelector("#console-accept");
const correctionForm = document.querySelector("#console-correction");
const correctionButton = document.querySelector("#console-correct");
const feedbackField = document.querySelector("#console-feedback");
const questionForm = document.querySelector("#console-question");
const questionText = document.querySelector("#console-question-text");
const answerField = document.querySelector("#console-answer");
const answerButton = document.querySelector("#console-send-answer");
const questionStatus = document.querySelector("#console-question-status");
const checkResults = document.querySelector("#console-check-results");
const reviewStatus = document.querySelector("#console-review-status");
const diffText = document.querySelector("#console-diff");
const lastPanelTask = "roleshuttle.last-r28-task";
const taskFields = {};
let projects = [];
let preparedTask;
let prepareRequest;
let reviewedDiff;
let busy = false;
let poll;

for (const [key, label, values, initial] of [
    ["type", "Task type", ["feature", "small_patch", "debug", "refactor", "docs", "tests", "search", "summarize", "architecture", "security_review", "integration", "gui_test"], "feature"],
    ["complexity", "Complexity", ["simple", "medium", "complex", "research"], "medium"],
    ["risk", "Risk", ["low", "medium", "high", "critical"], "low"],
    ["mode", "Work mode", ["write", "read"], "write"],
    ["role", "Role", ["specialist", "auto", "assistant", "coordinator", "implementer", "architect", "reviewer"], "specialist"],
]) {
    const input = select(values.map(value => [value, value]));
    input.value = initial;
    taskFields[key] = input;
    document.querySelector("#console-fields").append(labeled(label, input));
}

function renderProject() {
    const project = projects.find(item => item.id === projectSelect.value);
    for (const [selector, choices] of [
        ["#console-paths", project?.allowed_paths.map(path => [path, path]) ?? []],
        ["#console-checks", project?.checks.map(check => [check.id, check.title]) ?? []],
    ]) {
        const container = document.querySelector(selector);
        container.replaceChildren();
        for (const [value, label] of choices) {
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = value;
            container.append(labeled(label, checkbox));
        }
        if (!choices.length) container.textContent = "None configured";
    }
    prepareButton.disabled = !project || busy || Boolean(preparedTask);
}

async function loadProjects() {
    try {
        const result = await request(undefined, "/api/console-projects");
        if (!result.enabled) return;
        panel.hidden = false;
        const selected = projectSelect.value;
        projects = result.items;
        projectSelect.replaceChildren(new Option("Choose a project", ""));
        for (const project of projects) projectSelect.add(new Option(project.name, project.id));
        if (projects.some(project => project.id === selected)) projectSelect.value = selected;
        renderProject();
    } catch (error) {
        if (token) {
            panel.hidden = false;
            taskStatus.textContent = error.message;
        }
    }
}

function showTask(task) {
    if (preparedTask?.task_id !== task.task_id) {
        clearDiff();
        correctionForm.reset();
    } else if (preparedTask.attempt !== task.attempt) {
        clearDiff();
    }
    if (preparedTask?.owner_decision_request_id !== task.owner_decision_request_id) {
        questionForm.reset();
        questionStatus.textContent = "";
    }
    if (preparedTask?.task_id !== task.task_id || preparedTask?.revision !== task.revision) reconcileForm.reset();
    preparedTask = task;
    taskIdField.value = task.task_id;
    localStorage.setItem(lastPanelTask, task.task_id);
    const decision = task.decision;
    const route = [decision.assigned_provider, decision.assigned_model, decision.effort].filter(Boolean).join(" / ");
    taskStatus.textContent = [task.title, task.task_id, task.status, task.phase, route,
        task.attempt && `Attempt ${task.attempt}`,
        ...(task.reasons || decision.reason || [])].filter(Boolean).join(" · ");
    historyLink.href = task.history_url;
    historyLink.hidden = false;
    if (task.question_error) questionStatus.textContent = task.question_error;
    updateTaskControls();
    clearTimeout(poll);
    if (task.active && !task.requires_reconciliation) poll = setTimeout(refreshTask, 4000);
}

async function refreshTask() {
    const task = preparedTask;
    if (!task) return;
    if (busy) {
        poll = setTimeout(refreshTask, 4000);
        return;
    }
    try {
        const currentTask = await request({task_id: task.task_id}, "/api/console/status");
        if (preparedTask === task) showTask(currentTask);
    } catch (error) {
        if (preparedTask === task) {
            taskStatus.textContent = error.message;
            if (task.active && !task.requires_reconciliation) poll = setTimeout(refreshTask, 4000);
        }
    }
}

function updateTaskControls() {
    const task = preparedTask;
    const hasReview = reviewedDiff?.reviewable === true && !reviewedDiff.truncated;
    const waiting = Boolean(task?.owner_decision_pending);
    const questionBlocked = waiting || Boolean(task?.question_error);
    questionForm.hidden = !waiting;
    questionText.textContent = waiting ? task.owner_decision_question : "";
    answerField.disabled = busy;
    answerButton.disabled = busy || !task?.owner_decision_request_id || !answerField.value.trim();
    launchButton.disabled = busy || !task?.can_launch || task.decision.execution_admissible !== true;
    cancelButton.hidden = !task?.can_cancel;
    cancelButton.disabled = busy || !task?.can_cancel;
    reconcileForm.hidden = !task?.can_reconcile;
    reconcileButton.disabled = busy || !task?.can_reconcile || !localStopped.checked
        || !providerStopped.checked || reconcileReason.value.trim().length < 10;
    reviewSection.hidden = !(task?.can_diff || task?.phase === "checking" || reviewedDiff || (task?.active && task?.attempt));
    diffButton.disabled = busy || !task?.can_diff;
    checkButton.disabled = busy || questionBlocked || !task?.can_check || !hasReview;
    acceptButton.disabled = busy || questionBlocked || !task?.can_accept || !hasReview || task.fingerprint !== reviewedDiff.fingerprint;
    correctionForm.hidden = questionBlocked || !task?.can_check;
    feedbackField.disabled = busy;
    const feedbackLength = feedbackField.value.trim().length;
    correctionButton.disabled = busy || questionBlocked || !hasReview || !task?.correction_fingerprint
        || task.correction_fingerprint !== reviewedDiff.fingerprint || feedbackLength < 10 || feedbackLength > 8000;
    checkResults.replaceChildren(...(task?.checks || []).map(check => {
        const row = document.createElement("li");
        row.textContent = `${check.id} · ${check.status}`;
        return row;
    }));
}

function setTaskBusy(value) {
    busy = value;
    for (const field of taskForm.elements) field.disabled = value;
    for (const field of resumeForm.elements) field.disabled = value;
    for (const field of reconcileForm.elements) field.disabled = value;
    if (!value) {
        prepareButton.disabled = !projectSelect.value || Boolean(preparedTask);
    }
    updateTaskControls();
}

function clearDiff() {
    reviewedDiff = undefined;
    diffText.textContent = "";
    diffText.hidden = true;
    reviewStatus.textContent = "";
}

function clearPreparedTask() {
    clearTimeout(poll);
    preparedTask = undefined;
    prepareRequest = undefined;
    launchButton.disabled = true;
    prepareButton.disabled = !projectSelect.value;
    historyLink.hidden = true;
    taskStatus.textContent = "";
    correctionForm.reset();
    questionForm.reset();
    reconcileForm.reset();
    questionStatus.textContent = "";
    clearDiff();
    updateTaskControls();
}

taskForm.addEventListener("input", clearPreparedTask);
projectSelect.addEventListener("change", renderProject);
projectRefresh.addEventListener("click", async () => {
    clearPreparedTask();
    await loadProjects();
});
taskForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || preparedTask || !projectSelect.value) return;
    const checked = id => Array.from(document.querySelectorAll(`${id} input:checked`), field => field.value);
    const contract = {
        project_id: projectSelect.value,
        title: document.querySelector("#console-task-title").value.trim(),
        instructions: document.querySelector("#console-instructions").value.trim(),
        allowed_paths: checked("#console-paths"),
        check_ids: checked("#console-checks"),
        context_tokens: 8000,
        max_wall_time: document.querySelector("#console-minutes").valueAsNumber * 60,
        ...Object.fromEntries(Object.entries(taskFields).map(([key, input]) => [key, input.value])),
    };
    prepareRequest ??= crypto.randomUUID();
    setTaskBusy(true);
    taskStatus.textContent = "Preparing task in r28…";
    try {
        showTask(await request({request_id: prepareRequest, contract}, "/api/console/prepare"));
    } catch (error) {
        taskStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
        if (preparedTask) prepareButton.disabled = true;
    }
});
launchButton.addEventListener("click", async () => {
    if (busy || !preparedTask?.can_launch) return;
    const {task_id, revision} = preparedTask;
    setTaskBusy(true);
    taskStatus.textContent = "Starting r28 execution…";
    try {
        showTask(await request({task_id, revision}, "/api/console/launch"));
    } catch (error) {
        preparedTask.can_launch = false;
        taskStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
        prepareButton.disabled = true;
    }
});

cancelButton.addEventListener("click", () => {
    if (cancelButton.disabled) return;
    cancelTaskLabel.textContent = preparedTask.title;
    cancelScope.textContent = preparedTask.provider_cancel_ready
        ? "r28 will ask Codex to cancel the active response and wait for provider confirmation."
        : "r28 will stop the local execution and may require reconciliation of the provider request.";
    cancelDialog.returnValue = "";
    cancelDialog.showModal();
});

cancelDialog.addEventListener("close", async () => {
    if (cancelDialog.returnValue !== "stop" || busy || !preparedTask?.can_cancel) return;
    const {task_id, revision} = preparedTask;
    setTaskBusy(true);
    taskStatus.textContent = "Requesting cancellation in r28…";
    try {
        const task = await request({task_id, revision}, "/api/console/cancel");
        clearDiff();
        showTask(task);
    } catch (error) {
        await refreshTaskAfterConflict(task_id);
        taskStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
    }
});

reconcileForm.addEventListener("input", updateTaskControls);
reconcileForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (reconcileButton.disabled) return;
    const {task_id, revision} = preparedTask;
    const packet = {task_id, revision, local_stopped: localStopped.checked,
        provider_stopped: providerStopped.checked, reason: reconcileReason.value.trim()};
    setTaskBusy(true);
    try {
        showTask(await request(packet, "/api/console/reconcile"));
    } catch (error) {
        await refreshTaskAfterConflict(task_id);
        taskStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
    }
});

resumeForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    const task_id = taskIdField.value.trim();
    clearPreparedTask();
    setTaskBusy(true);
    try {
        showTask(await request({task_id}, "/api/console/status"));
    } catch (error) {
        taskStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
    }
});

diffButton.addEventListener("click", async () => {
    if (busy || !preparedTask?.can_diff) return;
    const task_id = preparedTask.task_id;
    clearDiff();
    setTaskBusy(true);
    try {
        const view = await request({task_id}, "/api/console/diff");
        if (view.revision !== preparedTask.revision) {
            showTask(await request({task_id}, "/api/console/status"));
            throw new Error("The task changed. Open its current diff.");
        }
        reviewedDiff = view;
        diffText.textContent = view.diff || "No text diff";
        diffText.hidden = false;
        const state = view.truncated ? "Diff is truncated" : view.reviewable ? "Current diff" : "Diff is not reviewable";
        reviewStatus.textContent = [state, ...(view.violations || [])].join(" · ");
        diffText.focus();
    } catch (error) {
        reviewStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
    }
});

async function submitReview(action, feedback) {
    const {task_id, revision} = preparedTask;
    const {fingerprint} = reviewedDiff;
    setTaskBusy(true);
    const messages = {
        check: ["Starting r28 checks…", "Checks started. See their status above."],
        accept: ["Accepting the checked result…", "Result accepted by r28."],
        correct: ["Sending feedback to r28…", "Correction started. Open the new diff when it is ready."],
    };
    const [pending, confirmed] = messages[action];
    reviewStatus.textContent = pending;
    try {
        const packet = {task_id, revision, fingerprint};
        if (action === "correct") packet.feedback = feedback;
        const task = await request(packet, "/api/console/" + action);
        if (action === "correct") {
            if (!task.active && task.attempt === preparedTask.attempt) {
                throw new Error(task.reasons?.join(" · ") || "r28 could not start the correction.");
            }
            clearDiff();
            correctionForm.reset();
        }
        showTask(task);
        reviewStatus.textContent = confirmed;
    } catch (error) {
        clearDiff();
        reviewStatus.textContent = error.message;
        await refreshTaskAfterConflict(task_id);
    } finally {
        setTaskBusy(false);
    }
}

async function refreshTaskAfterConflict(task_id) {
    try {
        showTask(await request({task_id}, "/api/console/status"));
    } catch (error) {
        taskStatus.textContent = error.message;
    }
}

checkButton.addEventListener("click", () => {
    if (!checkButton.disabled) submitReview("check");
});
acceptButton.addEventListener("click", () => {
    if (!acceptButton.disabled) submitReview("accept");
});
feedbackField.addEventListener("input", updateTaskControls);
correctionForm.addEventListener("submit", event => {
    event.preventDefault();
    if (!correctionButton.disabled) submitReview("correct", feedbackField.value.trim());
});
answerField.addEventListener("input", updateTaskControls);
questionForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (answerButton.disabled) return;
    const {task_id, revision, owner_decision_request_id: request_id} = preparedTask;
    const answer = answerField.value.trim();
    setTaskBusy(true);
    try {
        const task = await request({task_id, revision, request_id, answer}, "/api/console/answer");
        clearDiff();
        showTask(task);
        if (!task.question_error) {
            questionStatus.textContent = "Answer recorded in r28.";
        }
    } catch (error) {
        await refreshTaskAfterConflict(task_id);
        questionStatus.textContent = error.message;
    } finally {
        setTaskBusy(false);
    }
});

taskIdField.value = localStorage.getItem(lastPanelTask) || "";
load();
loadProjects();

const localRoot = document.querySelector("#local-tasks");
const localField = id => document.getElementById("local-" + id);
let localCatalog;
let localTask;
let localFingerprint;
let localBusy = false;

function displayLocalTask(task) {
    if (localTask?.task_id !== task.task_id || task.go_closure) {
        localField("go-close-comment").value = "";
    }
    if (localTask?.revision !== task.revision) {
        localFingerprint = undefined;
        localField("diff").hidden = true;
        localField("check-log").hidden = true;
        localField("check-log").textContent = "";
    }
    localTask = task;
    localField("controls").hidden = false;
    const base = task.contract.mode === "read" ? " · Base " + task.contract.base_revision.slice(0, 7) : "";
    localField("id").textContent = task.contract.title + " · " + task.task_id + base;
    localField("status").textContent = "Task status: " + task.status +
        (task.check_receipt?.passed ? " · Checks passed" : "");
    const operation=task.model_operation;
    if (operation?.interruption?.local_connection_closed) {
        localField("status").textContent += " · Go connection closed locally. Provider cancellation is unconfirmed.";
    }
    const goRequest = operation?.provider === "opencode_go" ? operation.go_request : undefined;
    const hasRequest = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(goRequest?.log_id || "");
    const hasWorkspace = hasRequest && /^wrk_[A-Za-z0-9]{1,80}$/.test(goRequest.workspace || "");
    localField("go-request").hidden = !hasRequest;
    localField("go-request-id").textContent = hasRequest ? goRequest.log_id : "";
    localField("go-request-hint").hidden = !hasRequest || hasWorkspace;
    const goLink = localField("go-request-link");
    goLink.hidden = !hasWorkspace;
    if (hasWorkspace) {
        const url = new URL("/console/" + goRequest.workspace + "/activity/logs", "https://opencode.ai");
        url.searchParams.set("log", goRequest.log_id);
        goLink.href = url.href;
    } else {
        goLink.removeAttribute("href");
    }
    const reconciliation = operation?.reconciliation;
    if (reconciliation) {
        const messages = {
            terminal_completion: "Provider reported a terminal completion (" + reconciliation.finish_reason + ").",
            http_error: "Provider lookup returned HTTP " + reconciliation.http_status + ".",
            no_terminal_evidence: "Provider lookup returned no matching terminal result.",
            unavailable: "Provider lookup was unavailable.",
            credential_unavailable: "The Go credential is unavailable.",
            extra_usage_unverified: "Could not confirm that Extra Usage is off. Lookup skipped.",
            invalid_response_id: "The saved Go response ID cannot be used for lookup.",
        };
        localField("status").textContent += " · " + messages[reconciliation.outcome] +
            " Checked " + new Date(reconciliation.checked_at).toLocaleString() + ".";
    }
    const closure = task.go_closure;
    const closureNote = localField("go-closure");
    closureNote.hidden = !closure;
    closureNote.textContent = closure
        ? "Closed manually " + new Date(closure.closed_at).toLocaleString() + "\n" + closure.comment : "";
    const input=task.panel_input;
    localField("route").textContent = operation ? [
        (operation.routing_category || "medium") + " · " + operation.route,
        operation.provider, operation.model,
        ...(operation.skipped_routes || []).map(row => "Skipped " + row.route + " (" + row.reason + ")"),
    ].join(" · ") : input?.candidates ? "Queued routes: " + input.candidates.map(row => row.route).join(" → ") : "";
    localField("question").textContent = task.phase === "waiting_model_input" ? task.model_operation.question : "";
    localField("message-label").hidden = !["waiting_model_input", "patch_applied"].includes(task.phase) || task.status === "accepted";
    localField("proof-label").hidden = task.contract.acceptance_mode !== "project" || task.status !== "review";
    refreshLocalControls();
}

function refreshLocalControls() {
    if (!localTask) return;
    const {phase, status, contract, panel_input, model_operation} = localTask;
    const review = phase === "patch_applied";
    const awaitingGoReview = phase === "model_unsettled" && status === "needs_review" &&
        model_operation?.provider === "opencode_go" && model_operation.interruption?.local_connection_closed &&
        model_operation.interruption.provider_acknowledged === false;
    localField("go-close-label").hidden = !awaitingGoReview;
    const visible = {
        launch: panel_input && (phase === "prepared" || phase === "worktree_ready" && !model_operation),
        status: true,
        cancel: phase === "requesting_model" &&
            (model_operation?.native_turn || model_operation?.go_stream) && !model_operation.cancel_requested_at,
        "reconcile-go": awaitingGoReview && model_operation.reconciliation?.outcome !== "terminal_completion",
        "close-go": awaitingGoReview,
        answer: phase === "waiting_model_input" && panel_input,
        apply: ["worktree_ready", "model_revision_ready"].includes(phase) && model_operation?.state === "returned",
        diff: review,
        check: review && status === "review" && localFingerprint,
        "check-baseline": contract.mode === "read" && status !== "needs_review" &&
            ["prepared", "worktree_ready", "baseline_checked"].includes(phase),
        "check-log": Boolean(localTask.last_check_log),
        revise: review && status === "review" && localFingerprint && panel_input && model_operation?.state === "returned",
        accept: review && status === "review" && localFingerprint && localTask.check_receipt?.passed,
    };
    for (const button of localField("actions").querySelectorAll("button")) {
        button.hidden = !visible[button.dataset.action];
        button.disabled = localBusy && !["status", "cancel"].includes(button.dataset.action);
    }
}

function selectedLocalProject() {
    return localCatalog.projects.find(row => row.id === localField("project").value);
}

function canPrepareLocal(project) {
    if (!project) return false;
    return project.allowed_categories
        ? project.allowed_categories.includes(localField("category").value)
        : project.model_allowed;
}
function updateLocalProjects() {
    const project = selectedLocalProject();
    localField("prepare").disabled = !canPrepareLocal(project);
    localField("prepare-baseline").disabled = !project?.has_checks;
    localField("paths").replaceChildren();
    for (const path of project?.paths || []) {
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = path;
        localField("paths").append(labeled(path, checkbox));
    }
}

async function loadLocalCatalog() {
    localCatalog = await request(undefined, "/api/local-tasks");
    if (!localCatalog.enabled) return;
    localRoot.hidden = false;
    const category=localField("category");
    const previousCategory=category.value || "medium";
    category.replaceChildren(...(localCatalog.routing_categories || ["medium"]).map(value => new Option(value,value)));
    category.value=previousCategory;

    const previous = localField("project").value;
    localField("project").replaceChildren(new Option("Choose a project", ""));
    for (const project of localCatalog.projects) {
        const option = new Option(project.name, project.id);
        option.disabled = !(project.allowed_categories?.length || project.model_allowed || project.has_checks);
        localField("project").add(option);
    }
    if (previous) localField("project").value = previous;
    const project = selectedLocalProject();
    localField("prepare").disabled = !canPrepareLocal(project);
    localField("prepare-baseline").disabled = !project?.has_checks;
    const history = localField("history");
    history.hidden = !localCatalog.history;
    if (localCatalog.history) history.href = localCatalog.history;
    const {assignment, configured_assignment: configured} = localCatalog;
    const execution = assignment.provider === "opencode_go"
        ? `Autonomous execution: OpenCode Go ${assignment.model_id}.`
        : `Autonomous execution: Codex ${assignment.model_id} · ${assignment.effort}.`;
    if (configured?.provider === "opencode_go" && assignment.provider !== "opencode_go") {
        localField("model").textContent = `Saved implementer: OpenCode Go ${configured.model_id}. ${execution} OpenCode Go credential is unavailable to RoleShuttle.`;
    } else {
        localField("model").textContent = localCatalog.ordered_routing
            ? "Ordered routing enabled. " + execution.replace("Autonomous execution:", "Base assignment:")
            : execution;
    }
    localField("records").replaceChildren(new Option("Open a task", ""));
    for (const task of localCatalog.tasks) {
        localField("records").add(new Option(task.title + " · " + task.status, task.id));
    }
    if (localTask) localField("records").value = localTask.task_id;
    if (taskTarget && /^[0-9a-f]{32}$/.test(taskTarget)) {
        localField("records").value = taskTarget;
        taskTarget = null;
        if (localField("records").value) localField("records").dispatchEvent(new Event("change"));
    }
}

localField("project").addEventListener("change", updateLocalProjects);
localField("category").addEventListener("change", () => {
    localField("prepare").disabled = !canPrepareLocal(selectedLocalProject());
});
localField("records").addEventListener("change", async event => {
    const taskId = event.target.value;
    if (!taskId) return;
    try { displayLocalTask(await request({task_id: taskId}, "/api/local/status")); }
    catch (error) { localField("status").textContent = error.message; }
});
localField("form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!canPrepareLocal(selectedLocalProject())) return;
    const button = event.submitter;
    button.disabled = true;
    try {
        const paths = [...localField("paths").querySelectorAll(":checked")].map(field => field.value);
        displayLocalTask(await request({project_id: localField("project").value, title: localField("task-title").value,
            prompt: localField("input").value, allowed_paths: paths, assignment: localCatalog.assignment,
            category: localField("category").value, request_id: crypto.randomUUID()}, "/api/local/prepare"));
        await loadLocalCatalog();
    } catch (error) { localField("status").textContent = error.message; }
    finally { button.disabled = !canPrepareLocal(selectedLocalProject()); }
});
localField("prepare-baseline").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        displayLocalTask(await request({project_id: localField("project").value,
            request_id: crypto.randomUUID()}, "/api/local/prepare-baseline"));
        await loadLocalCatalog();
    } catch (error) { localField("status").textContent = error.message; }
    finally { button.disabled = !selectedLocalProject()?.has_checks; }
});
localField("actions").addEventListener("click", async event => {
    const action = event.target.dataset.action;
    if (!action || !localTask) return;
    const exclusive = !["status", "cancel"].includes(action);
    if (exclusive && localBusy) return;
    const taskId = localTask.task_id;
    const operation = localTask.model_operation;
    const body = {task_id: taskId, fingerprint: localFingerprint, operation_id: operation?.id,
                  message: localField("message").value};
    if (action === "check-log") Object.assign(body, localTask.last_check_log);
    if (action === "close-go") {
        const comment = localField("go-close-comment");
        comment.setCustomValidity(comment.value.trim() ? "" : "Enter a closure comment.");
        if (!comment.reportValidity()) return;
        body.comment = comment.value.trim();
    }
    try {
        if (action === "accept" && localField("proof").value.trim()) body.proof = JSON.parse(localField("proof").value);
        if (exclusive) localBusy = true;
        refreshLocalControls();
        const result = await request(body, "/api/local/" + action);
        if (localTask?.task_id !== taskId) return;
        if (action === "diff") {
            localFingerprint = result.fingerprint;
            localField("diff").textContent = result.diff;
            localField("diff").hidden = false;
        } else if (action === "check-log") {
            const log = localField("check-log");
            const header = result.check_id + " · Receipt " + result.receipt_id;
            const notice = result.truncated ? "\nEarlier output omitted." : "";
            log.textContent = header + notice + "\n\n" + (result.text || "The check produced no output.");
            log.hidden = false;
            log.focus();
        } else {
            displayLocalTask(result);
            if (action === "check") localFingerprint = result.check_receipt.fingerprint;
            if (action !== "status") await loadLocalCatalog();
        }
    } catch (error) { localField("status").textContent = error.message; }
    finally {
        if (exclusive) localBusy = false;
        refreshLocalControls();
    }
});
setInterval(async () => {
    if (!localTask || (!localBusy && !localTask.active)) return;
    const taskId = localTask.task_id;
    try {
        const task = await request({task_id: taskId}, "/api/local/status");
        if (localTask?.task_id === taskId && task.revision !== localTask.revision) displayLocalTask(task);
    } catch { /* The explicit Refresh action reports errors. */ }
}, 850);
const archiveTasks = document.querySelector("#archive-tasks");
const archiveStatus = document.querySelector("#archive-status");
const archiveCard = document.querySelector("#archive-card");

async function loadArchive() {
    const archive = document.querySelector("#r28-archive");
    try {
        const {items} = await request({}, "/api/local/archive-list");
        if (!items.length) return;
        archiveTasks.replaceChildren(new Option("Open an archived task", ""));
        for (const item of items) {
            archiveTasks.add(new Option(item.title + " · " + item.status, item.task_id));
        }
        archive.hidden = false;
        if (archiveTarget && /^[0-9a-f]{32}$/.test(archiveTarget)) {
            archiveTasks.value = archiveTarget;
            if (archiveTasks.value) archiveTasks.dispatchEvent(new Event("change"));
            else archiveStatus.textContent = "Archived task not found.";
        }
    } catch (error) {
        archive.hidden = false;
        archiveTasks.disabled = true;
        archiveStatus.textContent = error.message;
    }
}

archiveTasks.addEventListener("change", async () => {
    const taskId = archiveTasks.value;
    archiveCard.hidden = true;
    archiveStatus.textContent = "";
    if (!taskId) return;
    try {
        const task = await request({task_id: taskId}, "/api/local/archive-card");
        if (archiveTasks.value !== taskId) return;
        document.querySelector("#archive-card-title").textContent = task.title;
        document.querySelector("#archive-card-id").textContent = task.task_id + " · " + task.status;
        document.querySelector("#archive-receipts").textContent = task.receipts.length
            ? JSON.stringify(task.receipts, null, 2) : "No attempt receipts were saved.";
        document.querySelector("#archive-diff-status").textContent =
            task.diff?.truncated ? "The saved diff is truncated." : "";
        document.querySelector("#archive-diff").textContent =
            task.diff ? task.diff.diff || "No changes in the saved diff." : "No saved diff is available.";
        archiveCard.hidden = false;
    } catch (error) {
        if (archiveTasks.value === taskId) archiveStatus.textContent = error.message;
    }
});

loadLocalCatalog()
    .then(() => { if (localCatalog.enabled) return loadArchive(); })
    .catch(error => { if (!localRoot.hidden) localField("status").textContent = error.message; });

const advancedField = name => document.getElementById("advanced-" + name);
const advancedSection = document.getElementById("advanced-settings");
let advancedCurrent;
function displayAdvanced(settings) {
    for (const [id, key] of [["routes", "routes"], ["route-catalog", "route_catalog"],
                             ["fallbacks", "fallbacks"], ["local-servers", "local_servers"]]) {
        advancedField(id).value = JSON.stringify(settings[key], null, 2);
    }
    advancedField("health").value = settings.timeouts.local_health_seconds;
    advancedField("task-default").value = settings.timeouts.task_default_seconds;
    advancedField("task-max").value = settings.timeouts.task_max_seconds;
    advancedField("go-restrict").checked = settings.go_models !== null;
    advancedField("go-models").value = (settings.go_models || []).join("\n");
    advancedField("go-workspace").value = settings.go_console_workspace || "";
}
function updateAdvanced(current) {
    advancedCurrent = current;
    displayAdvanced(current.settings);
    const older = current.history.filter(item => item.revision !== current.revision);
    advancedField("revisions").replaceChildren(...older.map(item =>
        new Option(item.saved_at + " · " + item.revision.slice(0, 8), item.revision)));
    advancedField("local-selected").replaceChildren(...current.settings.local_servers.map(item =>
        new Option(item.id,item.id)));
    advancedField("local-refresh").disabled = !current.settings.local_servers.length;
    advancedField("save").disabled = false;
    advancedField("restore").disabled = !older.length;
}
async function reloadAdvanced() {
    try {
        updateAdvanced(await request(undefined, "/api/advanced-settings"));
        advancedField("status").textContent = "Saved advanced settings loaded.";
    } catch (error) {
        advancedField("status").textContent = error.message;
    }
}
advancedSection.addEventListener("toggle", () => {
    if (advancedSection.open && !advancedCurrent) reloadAdvanced();
});
advancedField("reload").addEventListener("click", reloadAdvanced);
advancedField("preview-import").addEventListener("click", async () => {
    const file = advancedField("import").files[0];
    try {
        if (!file || file.size > 65_536) throw new Error("Choose an offline import file up to 64 KiB.");
        displayAdvanced(JSON.parse(await file.text()));
        advancedField("status").textContent = "Import loaded into the draft. Save to apply it.";
    } catch (error) { advancedField("status").textContent = error.message; }
});
async function writeAdvanced(body, message) {
    const saveButton = advancedField("save");
    const restoreButton = advancedField("restore");
    saveButton.disabled = restoreButton.disabled = true;
    try {
        updateAdvanced(await request({revision: advancedCurrent.revision, ...body}, "/api/advanced-settings"));
        advancedField("status").textContent = message;
    } catch (error) {
        advancedField("status").textContent = error.message;
    } finally {
        saveButton.disabled = !advancedCurrent;
        restoreButton.disabled = !advancedField("revisions").options.length;
    }
}
advancedField("save").addEventListener("click", async () => {
    try {
        const parse = name => JSON.parse(advancedField(name).value);
        const settings = {routes: parse("routes"), route_catalog: parse("route-catalog"),
            fallbacks: parse("fallbacks"), local_servers: parse("local-servers"),
            timeouts: {local_health_seconds: Number(advancedField("health").value),
                task_default_seconds: Number(advancedField("task-default").value),
                task_max_seconds: Number(advancedField("task-max").value)},
            go_console_workspace: advancedField("go-workspace").value.trim(),
            go_models: advancedField("go-restrict").checked ?
                advancedField("go-models").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean) : null};
        await writeAdvanced({settings}, "Advanced settings saved.");
    } catch (error) { advancedField("status").textContent = error.message; }
});
advancedField("restore").addEventListener("click", () => {
    writeAdvanced({restore: advancedField("revisions").value}, "Advanced settings restored.");
});
advancedField("go-refresh").addEventListener("click", async () => {
    const button = advancedField("go-refresh");
    button.disabled = true;
    try {
        const status = await request({}, "/api/advanced-go-status");
        if (!status.configured) {
            advancedField("go-status").textContent = "OpenCode Go credential unavailable.";
        } else {
            const quota = Object.entries(status.quota).map(([window, meter]) =>
                window + ": " + meter.percent + "% used (" + meter.status + ")");
            advancedField("go-status").textContent = [
                status.extra_usage_off ? "Extra Usage is off." : "Extra Usage off is unconfirmed.",
                ...quota, "Discovered models:", ...status.models,
            ].join("\n");
        }
    } catch (error) { advancedField("go-status").textContent = error.message; }
    finally { button.disabled = false; }
});


advancedField("local-refresh").addEventListener("click", async () => {
    const button = advancedField("local-refresh");
    button.disabled = true;
    const output = advancedField("local-status");
    try {
        const status = await request({server_id: advancedField("local-selected").value}, "/api/advanced-local-status");
        output.textContent = status.available ? [
            "Connected.", ...status.models.map(model => [model.id,
              model.loaded ? "loaded" : model.loading ? "loading" : "available",
              model.allowed ? "allowed" : "outside saved list"].join(" · ")),
        ].join("\n") : status.reason;
    } catch (error) { output.textContent = error.message; }
    finally { button.disabled = !advancedField("local-selected").options.length; }
});
