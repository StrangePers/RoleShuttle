"""Assets for the dedicated checkout registration page."""

HTML = b"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Register checkout | RoleShuttle</title>
<style>
body { font: 1rem system-ui, sans-serif; margin: 2rem auto; max-width: 44rem; padding: 0 1rem; }
label { display: block; margin: .6rem 0; }
input[type=text], textarea { display: block; width: 100%; box-sizing: border-box; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
<h1>Register checkout</h1>
<p>Review and confirm the project profile, then return to the terminal to continue.</p>
<form>
  <fieldset disabled>
    <legend>Project profile</legend>
    <label>Git root <input name="path" type="text" readonly></label>
    <label>Project ID <input name="projectId" type="text" required></label>
    <p id="source-project" hidden></p>
    <fieldset id="permissions" style="border: 0; margin: 0; padding: 0; min-width: 0;">
      <label>Allowed write paths (one per line) <textarea name="paths"></textarea></label>
      <label><input name="worktrees" type="checkbox"> Allow linked worktrees</label>
      <label><input name="sharedMac" type="checkbox"> Allow shared Mac</label>
      <label><input name="externalCode" type="checkbox"> Allow external code access</label>
      <label><input name="codex" type="checkbox"> Allow automatic Codex</label>
      <label><input name="opencodeGo" type="checkbox"> Allow automatic OpenCode Go</label>
    </fieldset>
    <button type="submit">Preview project</button>
    <pre hidden></pre>
    <label><input name="consent" type="checkbox" disabled> I approve the project profile shown above</label>
    <button id="confirm" type="button" disabled>Register project</button>
  </fieldset>
  <button id="cancel" type="button" disabled>Cancel registration</button>
</form>
<p role="status"></p>
<script src="/checkout.js" defer></script>
"""

JS = br"""const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
history.replaceState(null, "", location.pathname);
const form = document.querySelector("form");
const fields = form.elements;
const fieldset = form.querySelector("fieldset");
const summary = form.querySelector("pre");
const confirm = document.getElementById("confirm");
const cancel = document.getElementById("cancel");
const status = document.querySelector('[role="status"]');
const permissions = document.getElementById("permissions");
let previewId;

async function request(path, payload) {
    const headers = {Authorization: `Bearer ${token}`};
    const options = {headers};
    if (payload !== undefined) {
        headers["Content-Type"] = "application/json";
        Object.assign(options, {method: "POST", body: JSON.stringify(payload)});
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    return data;
}

function invalidate() {
    previewId = null;
    summary.hidden = fields.consent.disabled = confirm.disabled = true;
    fields.consent.checked = false;
}

async function interact(action) {
    fieldset.disabled = cancel.disabled = true;
    try {
        if (await action()) return;
    } catch (error) {
        status.textContent = error.message;
    }
    fieldset.disabled = cancel.disabled = false;
}

form.addEventListener("input", event => {
    if (event.target === fields.consent) confirm.disabled = !previewId || !fields.consent.checked;
    else invalidate();
});
form.addEventListener("submit", event => {
    event.preventDefault();
    invalidate();
    interact(async () => {
        const payload = {path: fields.path.value, id: fields.projectId.value};
        if (!permissions.disabled) {
            const automatic_cloud = [];
            if (fields.codex.checked) automatic_cloud.push("codex");
            if (fields.opencodeGo.checked) automatic_cloud.push("opencode_go");
            Object.assign(payload, {
                allowed_paths: fields.paths.value.split("\n").map(path => path.trim()).filter(Boolean),
                worktrees_allowed: fields.worktrees.checked, shared_mac_allowed: fields.sharedMac.checked,
                external_code_allowed: fields.externalCode.checked, automatic_cloud,
            });
        }
        const {preview_id, project} = await request("/api/local/project-preview", payload);
        previewId = preview_id;
        summary.textContent = JSON.stringify(project, null, 2);
        summary.hidden = fields.consent.disabled = false;
        status.textContent = "Review the profile and check the approval box to register it.";
    });
});
confirm.onclick = () => interact(async () => {
    if (!previewId || !fields.consent.checked) return;
    const payload = {preview_id: previewId, confirmed: true};
    invalidate();
    const project = await request("/api/local/project-confirm", payload);
    status.textContent = `Registered ${project.id}. Return to the terminal to continue.`;
    return true;
});
cancel.onclick = () => interact(async () => {
    await request("/api/checkout/cancel", {});
    status.textContent = "Registration cancelled. Return to the terminal.";
    return true;
});
request("/api/checkout").then(({path, id, inherited, source_project_id}) => {
    fields.path.value = path;
    fields.projectId.value = id;
    if (inherited) {
        const source = document.getElementById("source-project");
        source.textContent = `Permissions inherited from project ${source_project_id}.`;
        source.hidden = false;
        permissions.hidden = permissions.disabled = true;
    }
    fieldset.disabled = cancel.disabled = false;
}).catch(error => { status.textContent = error.message; });
"""
