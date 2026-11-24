"""Offline Recommended routing preview compatible with r28 first attempts."""

from datetime import datetime
from math import isfinite
from pathlib import Path
import json
import re

RESERVE = 15
GO_MODELS = {
    "kimi-k2.7-code": (3, 0), "glm-5.3-flash": (3, 3), "qwen3.8-flash": (3, 4),
    "minimax-m3.1-flash": (3, 5), "qwen3.7-plus": (3, 6), "kimi-k3.1-pro": (4, 0),
    "glm-5.3": (4, 2), "qwen4-max": (4, 4), "qwen3.8-max": (4, 5),
    "gemini-4-argon": (4, 6), "deepseek-v4-pro": (4, 7),
}
ROLE_ORDER = ("assistant", "coordinator", "implementer", "specialist", "architect", "reviewer")
ROLE_ROUTES = dict(zip(ROLE_ORDER, ("codex_assistant", "codex_coordinator", "codex_engineer",
                                  "codex_specialist", "codex_critical", "codex_reviewer")))
FLOORS = dict(zip(ROLE_ORDER, (1, 3, 3, 3, 4, 4)))
LANE_ROLES = {"bounded": "assistant", "medium": "implementer", "complex": "specialist",
              "high": "specialist", "critical": "reviewer", "architecture": "architect"}
BOUNDED_TYPES = {"search", "summarize", "docs", "tests", "small_patch", "refactor", "debug"}
TASK_TYPES = BOUNDED_TYPES | {"feature", "architecture", "security_review", "integration", "gui_test"}
FAVORITES = {
    "opencode": ("deepseek-v4.1-flash-free", "glm-5.3-flash-free", "longcat-2.5-free"),
    "openrouter": ("cohere/north-mini-code:free", "nvidia/nemotron-3-ultra-550b-a55b:free"),
}


def number(value):
    return type(value) in {int, float} and isfinite(value)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def instant(value):
    return value if number(value) else timestamp(value)


def fresh(value, now, age):
    observed = timestamp(value)
    return observed is not None and 0 <= now - observed <= age


def pool_pressure(pool, now, max_age):
    used = pool.get("used_percent")
    known = (pool.get("status") == "known" and number(used) and 0 <= used < 100
             and fresh(pool.get("observed_at"), now, max_age))
    result = {"known": known, "used_percent": used if number(used) else None,
              "pressure": None, "resets_at": [], "reason": None}
    if not known:
        return {**result, "reason": "Квота исчерпана, неизвестна или устарела"}
    windows = pool.get("windows", [])
    if isinstance(windows, dict):
        windows = list(windows.values())
    pressures = []
    for window in windows if isinstance(windows, list) else []:
        if not isinstance(window, dict):
            continue
        consumed = window.get("used_percent")
        if not number(consumed) or not 0 <= consumed < 100:
            return {**result, "known": False, "reason": "Нет достоверного остатка одного из окон"}
        reset = instant(window.get("resets_at"))
        if reset is None and consumed == 0:
            continue
        if reset is None or reset <= now:
            return {**result, "known": False, "reason": "Истекло окно наблюдения квоты"}
        start = instant(window.get("starts_at"))
        minutes = window.get("window_minutes")
        if start is None and number(minutes) and minutes > 0:
            start = reset - minutes * 60
        pressure = consumed
        if start is not None and start <= now < reset:
            target = (100 - RESERVE) * (now - start) / (reset - start)
            pressure = max(0, 2 * consumed - target)
        result["resets_at"].append(reset)
        pressures.append(pressure)
    result["pressure"] = round(max(pressures) if pressures else used, 2)
    return result


def task_lane(task):
    if task["risk"] == "critical" or task["type"] == "security_review":
        return "critical"
    if task["type"] == "architecture":
        return "architecture"
    if task["risk"] == "high":
        return "high"
    if task["complexity"] == "complex" or task["type"] == "integration":
        return "complex"
    if task.get("bounded") is True and task.get("machine_checkable") is True and task["type"] in BOUNDED_TYPES:
        return "bounded"
    return "medium"


def role_rank(role):
    return min(ROLE_ORDER.index(role), 4)


def configured_candidates(lane_routes, role, profiles):
    if not profiles:
        return list(lane_routes)
    candidates = []
    for key in lane_routes:
        if key == "codex_engineer":
            candidates.append(ROLE_ROUTES[role])
            if role_rank(role) < 2:
                candidates.append("codex_engineer")
        elif key == "codex_critical":
            candidates.append(ROLE_ROUTES[role] if role_rank(role) >= 4 else "codex_critical")
        else:
            candidates.append(key)
    return list(dict.fromkeys(candidates))


def free_candidates(sources, now, context):
    for provider, favorites in FAVORITES.items():
        snapshot = sources.get(provider, {})
        stamp = snapshot.get("observed_at")
        if snapshot.get("state") != "ok" or not fresh(stamp, now, 300):
            continue
        models = [row for row in snapshot.get("models", []) if isinstance(row, dict)
                  and row.get("provider") == provider and row.get("tools") is True and isinstance(row.get("id"), str)]
        ranking = dict(zip(favorites, range(len(favorites))))
        seen = set()
        for row in sorted(models, key=lambda row: (ranking.get(row["id"], len(ranking)), row["id"])):
            model_id = row["id"]
            pattern = (r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,150}-free" if provider == "opencode"
                       else r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+:free")
            if len(model_id) > 200 or not re.fullmatch(pattern, model_id) or model_id in seen:
                continue
            seen.add(model_id)
            capacity = row.get("context_tokens")
            if type(capacity) is not int or capacity < max(1, context):
                continue
            model = provider + "/" + model_id
            yield ("free:" + model, {"worker": "opencode", "provider": provider + "-free", "model": model,
                                      "tier": 1, "pool": None, "boundary": "external", "free_adapter": True},
                   {"available": True, "model": model, "adapter_available": True, "capabilities": ["read", "edit"],
                    "context_tokens": capacity, "observed_at": stamp})
            break


def budget_matches(route, pool, task, now, age):
    paid = pool.get("extra_usage", {})
    return (task.get("allow_extra_usage") is True and paid.get("admissible") is True
            and paid.get("budget_id") and route["model"] in paid.get("admitted_models", [])
            and fresh(paid.get("observed_at"), now, age))


def pool_reasons(route, pool, task, policy, lane, now, observed):
    hard, admission = [], []
    if route["pool"] is None:
        return hard, admission
    if policy.get("admission", {}).get("mode") == "provider_authoritative":
        if route["pool"] == "opencode_go" and not (
                task.get("allow_extra_usage") is True and policy["extra_usage_supported"] is True):
            admission.append("explicit Extra Usage permission required")
        return hard, admission
    age = policy["snapshot_max_age_seconds"]
    paid = budget_matches(route, pool, task, now, age)
    if observed.get("requires_guarded_budget") is True and not paid:
        guarded = ["native Go adapter requires the existing shared budget admission for this exact model"]
    else:
        guarded = []
    if route["pool"] == "opencode_go" and policy["extra_usage_supported"] is True and paid:
        return hard, admission
    if pool.get("status") == "exhausted":
        hard.append("subscription pool exhausted; changing model cannot clear the circuit breaker")
    if "expires_at" in pool:
        expires = timestamp(pool["expires_at"])
        if expires is None or expires <= now:
            admission.append("subscription observation window ended; refresh required")
    if pool.get("extra_usage_disabled") is not True:
        admission.append("extra usage must be confirmed disabled; this adapter does not authorize paid overage")
    used = pool.get("used_percent")
    if not (pool.get("status") == "known" and number(used) and 0 <= used <= 100
            and fresh(pool.get("observed_at"), now, age)):
        admission.append("subscription quota unknown/stale; plan only")
    else:
        limits, priority = policy["quota_thresholds"], task.get("priority")
        if used >= limits["stop"]:
            hard.append("subscription pool at stop threshold")
        elif used >= limits["blocking_only"] and priority != "blocking":
            hard.append("quota permits blocking tasks only")
        elif used >= limits["restrict"] and (priority not in {"high", "blocking"}
                                             or lane not in {"complex", "high", "critical", "architecture"}):
            hard.append("quota reserved for difficult high-priority work")
        elif used >= limits["warn"] and priority == "background":
            hard.append("quota warning: background work stays local or waits")
    return hard, admission + guarded


def project_route_reasons(project, route, task, now):
    subscription = {"codex": "codex", "opencode-go": "opencode_go"}.get(route["provider"])
    if subscription and (subscription not in project["automatic_cloud"]
                         or route["pool"] != subscription or route["boundary"] != "external"):
        return ["subscription route is not authorized by the project"]
    if route["provider"] == "mac-omlx" or route["boundary"] == "shared_mac":
        evidence = project.get("local_capability") or {}
        verified = timestamp(evidence.get("verified_at"))
        if (route["boundary"] != "shared_mac" or evidence.get("model") != route["model"]
                or verified is None or verified > now
                or not set(task["required_tools"]).issubset(evidence.get("capabilities", []))
                or not local_evidence_matches(project, evidence)):
            return ["the exact local model and tools need intact project capability evidence"]
    return []


def local_evidence_matches(project, evidence):
    from .check_runner import executable_hash

    try:
        file = Path(evidence["evidence"])
        return (file.is_absolute() and not file.resolve().is_relative_to(Path(project["repository"]))
                and executable_hash(file) == evidence["evidence_sha256"])
    except (KeyError, TypeError, ValueError, OSError):
        return False


def assess_route(route, observed, task, policy, analysis, lane, now):
    hard, admission = [], []
    floor, pool = analysis["minimum_tier"], route["pool"]
    if route["tier"] < floor and not (route["provider"] == "mac-omlx" and floor == 1):
        hard.append("Модель ниже уровня роли в Recommended")
    elif pool is not None:
        quota = analysis["pools"].get(pool, {})
        if not quota.get("known"):
            hard.append(quota.get("reason") or "Нет достоверной квоты подписки")
        elif quota["used_percent"] >= 100 - RESERVE and task.get("priority") not in {"high", "blocking"}:
            hard.append("Остаток подписки сохранён для задач высокого приоритета")
    if route["boundary"] == "external" and task.get("external_code_allowed") is not True:
        hard.append("project disallows external code transfer")
    if route["boundary"] == "shared_mac" and task.get("shared_mac_allowed") is not True:
        hard.append("shared Mac data boundary not authorized")
    if not fresh(observed.get("observed_at"), now, policy["snapshot_max_age_seconds"]):
        hard.append("capability snapshot unknown/stale")
    if observed.get("available") is not True or observed.get("model") != route["model"]:
        hard.append("exact configured model is not confirmed available")
    if policy["schema_version"] == 2 and pool == "codex" and observed.get("effort") != route.get("effort"):
        hard.append("configured reasoning has not been verified for this model")
    if observed.get("adapter_available") is not True:
        admission.append("execution adapter unavailable; plan only")
    needed = set(task["required_tools"])
    if task["type"] == "gui_test":
        needed.update({"vision", "gui"})
    capabilities = observed.get("capabilities", [])
    if not isinstance(capabilities, list) or not needed.issubset(capabilities):
        hard.append("required tools/vision/GUI capabilities not verified")
    capacity = observed.get("context_tokens")
    if type(capacity) is not int or capacity <= 0 or task.get("context_tokens", 0) > capacity:
        hard.append("context capacity is unverified or insufficient")
    pool_hard, pool_admission = pool_reasons(route, analysis["raw_pools"].get(pool, {}),
                                             task, policy, lane, now, observed)
    hard.extend(pool_hard)
    admission.extend(pool_admission)
    return {"eligible": not hard, "execution_admissible": not hard and not admission,
            "reasons": hard + admission}


def score(route, analysis):
    pool = route["pool"]
    if pool is None:
        return 5 if route.get("free_adapter") else 0
    pressure = analysis["pools"].get(pool, {}).get("pressure")
    return 25 + (pressure if pressure is not None else 200) + max(
        0, route["tier"] - analysis["minimum_tier"]) * 20 + route.get("recommended_rank", 0)


def preview(task, policy, availability, baseline_routing, *, now, project=None):
    """Compare configured candidates against snapshots. Never grants launch permission."""
    if project is not None:
        task = {**task, **{field: task.get(field) is True and project.get(field) is True
                           for field in ("external_code_allowed", "shared_mac_allowed")},
                "allow_extra_usage": task.get("allow_extra_usage") is True and project["extra_usage_allowed"]}
    profiles = policy.get("schema_version") == 2
    requested = task.get("role", "auto")
    if (policy.get("schema_version") not in (1, 2) or type(policy.get("extra_usage_supported")) is not bool
            or policy.get("activate_saved_resets") is not False or not number(now)
            or task.get("type") not in TASK_TYPES or task.get("risk") not in {"low", "medium", "high", "critical"}
            or task.get("complexity") not in {"simple", "medium", "complex", "research"}
            or task.get("priority", "normal") not in {"normal", "high", "blocking", "background"}
            or not isinstance(task.get("required_tools"), list)
            or not all(isinstance(tool, str) for tool in task["required_tools"])
            or type(task.get("context_tokens", 0)) is not int or task.get("context_tokens", 0) < 0
            or requested not in (*ROLE_ORDER, "auto") or not profiles and requested != "auto"):
        raise ValueError("Invalid task or Recommended preview policy")
    if task.get("attempts"):
        raise ValueError("Recommended preview currently supports first attempts only")
    if not isinstance(task.get("dependencies", {}), dict):
        raise ValueError("Expected task dependencies as an object")
    result = {"status": "blocked", "selected_route": None, "assigned_model": None, "assigned_provider": None,
              "execution_admissible": False, "executed": False, "mode": "plan-only", "alternatives": [],
              "review_level": "independent_strong" if task["risk"] in {"high", "critical"} else "controller"}
    if any(status != "accepted" for status in task.get("dependencies", {}).values()):
        return {**result, "reason": ["dependencies are not accepted"]}
    lane = task_lane(task)
    minimum = LANE_ROLES[lane]
    role = requested if requested != "auto" and role_rank(requested) >= role_rank(minimum) else minimum
    narrowed = set(policy["routing"][lane]) != set(baseline_routing[lane])
    candidates = configured_candidates(policy["routing"][lane], role, profiles)
    routes = dict(policy["routes"])
    observations = dict(availability.get("routes", {}))
    if not narrowed:
        for model, (tier, rank) in GO_MODELS.items():
            if model in policy.get("go_model_allowlist", []):
                key = "recommended-go/" + model
                routes[key] = {"worker": "opencode", "provider": "opencode-go", "model": "opencode-go/" + model,
                               "tier": tier, "pool": "opencode_go", "boundary": "external", "recommended_rank": rank}
                if key not in candidates:
                    candidates.append(key)
        if (lane in {"bounded", "medium"} and task.get("bounded") is True
                and task.get("external_code_allowed") is True and task["type"] != "gui_test"
                and (not profiles or role_rank(role) <= 2) and set(task["required_tools"]) <= {"read", "edit"}):
            for key, route, observed in free_candidates(availability.get("free_sources", {}), now, task.get("context_tokens", 0)):
                routes[key], observations[key] = route, observed
                candidates.append(key)
    raw_pools = availability.get("pools", {})
    explanation = "Сначала уровень роли и допуск, затем бесплатные ресурсы и темп расхода окон подписок"
    analysis = {"reserve_percent": RESERVE, "minimum_tier": FLOORS[role], "lane_narrowed": narrowed,
                "pools": {pool: pool_pressure(raw_pools.get(pool, {}), now, min(300, policy["snapshot_max_age_seconds"]))
                          for pool in ("codex", "opencode_go")}, "explanation": explanation}
    result["recommended"] = analysis
    if profiles:
        result["role"] = role
    for key in candidates:
        route = routes[key]
        observed = {"observed_at": availability.get("observed_at"), **observations.get(key, {})}
        assessment = assess_route(route, observed, task, policy, {**analysis, "raw_pools": raw_pools}, lane, now)
        if project is not None:
            blocked = project_route_reasons(project, route, task, now)
            if blocked:
                assessment = {"eligible": False, "execution_admissible": False,
                              "reasons": blocked + assessment["reasons"]}
        result["alternatives"].append({"route": key, "model": route["model"], **assessment,
                                       "recommended_score": score(route, analysis)})
    eligible = sorted((row for row in result["alternatives"] if row["eligible"]),
                      key=lambda row: (not row["execution_admissible"], row["recommended_score"]))
    if not eligible:
        reason = "no eligible route; no worker launched" if candidates else "В активном пресете нет допущенного маршрута для этой задачи"
        return {**result, "reason": [reason]}
    choice = eligible[0]
    route = routes[choice["route"]]
    if profiles:
        result.update(role=route.get("role", role), requested_role=requested)
    return {**result, "status": "planned", "selected_route": choice["route"], "assigned_model": route["model"],
            "assigned_provider": route["provider"], "effort": route.get("effort"), "worker": route["worker"],
            "execution_admissible": choice["execution_admissible"],
            "reason": [explanation, "Выбран допустимый маршрут с минимальным давлением на квоты", *choice["reasons"]],
            **({"free_adapter": True} if route.get("free_adapter") else {})}


def preview_snapshot(snapshot, *, project_id=None):
    project = None
    if project_id is not None:
        from .local_tasks import database, load_project

        with database() as db:
            project = load_project(db, project_id)
    try:
        availability = snapshot["availability"]
        if project and project.get("availability_file"):
            file = Path(project["availability_file"])
            if (not file.is_absolute() or file.resolve().is_relative_to(Path(project["repository"]))
                    or file.stat().st_size > 2_000_000):
                raise ValueError("Project observations must come from a bounded operator-owned file")
            availability = json.loads(file.read_text(encoding="utf-8-sig"))
        result = preview(snapshot["task"], snapshot["policy"], availability,
                         snapshot["baseline_routing"], now=snapshot["now"], project=project)
    except (AttributeError, KeyError, TypeError, OverflowError):
        raise ValueError("Expected a complete Recommended preview snapshot") from None
    if project is not None:
        result.update(project_id=project_id, project_revision=project["revision"],
                      extra_usage_allowed=project["extra_usage_allowed"])
    return result
