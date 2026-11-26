"""Plan and admit a task's ordered model candidates before transmitting its prompt."""
from .advanced_settings import LANES

SUPPORTED = {"codex", "opencode_go"}


def plan(category, model_settings, settings, primary):
    if category not in LANES:
        raise ValueError("Choose a supported routing category")

    candidates = []
    seen = set()

    def add(route, provider, model, effort=None, boundary="external"):
        identity = (provider, model, effort, boundary)
        if identity not in seen:
            candidates.append({"route":route, "provider":provider, "model":model,
                               "effort":effort, "boundary":boundary})
            seen.add(identity)

    for route in settings["routes"].get(category, []):
        row = settings["route_catalog"][route]
        provider = "opencode_go" if row["provider"] == "opencode-go" else row["provider"]
        model = row["model"].removeprefix("opencode-go/") if provider == "opencode_go" else row["model"]
        add(route, provider, model, row.get("effort", "medium" if provider == "codex" else None), row["boundary"])

    def add_assignment(route, assignment):
        if assignment:
            add(route, assignment["provider"], assignment["model_id"], assignment.get("effort"))

    add_assignment("category:"+category, model_settings["lanes"].get(category))
    add_assignment("role:implementer", primary)
    fallback = settings["fallbacks"]
    for group, name in (("lanes",category),("roles","implementer")):
        for index, entry in enumerate(fallback[group].get(name,[]),1):
            add_assignment(f"fallback:{group}:{name}:{index}", entry)
    return candidates


def eligible(candidate, project):
    if candidate["provider"] == "mac-omlx":
        evidence=project.get("local_capability") or {}
        return (candidate["boundary"]=="shared_mac" and project.get("shared_mac_allowed") is True
                and evidence.get("model")==candidate["model"]
                and {"read","edit"}.issubset(evidence.get("capabilities") or []))
    return (candidate["provider"] in SUPPORTED and candidate["boundary"] == "external"
            and project["external_code_allowed"]
            and candidate["provider"] in project.get("automatic_cloud", []))


def choose(candidates, project, observe, *, existing=None):
    """Only the callback's metadata requests are allowed here."""
    if existing:
        keys=("route","provider","model","effort")
        candidates=[row for row in candidates if all(row[k]==existing[k] for k in keys)]
    rejected=[]
    for row in candidates:
        if not eligible(row,project):
            reason="unsupported adapter or project permission"
        else:
            try:
                return row,observe(row),rejected
            except (ValueError, OSError, RuntimeError, TimeoutError):
                reason="provider admission or model availability"
        rejected.append({**row,"reason":reason})
    raise ValueError("No admissible route in the saved selection. No model request was sent.")
