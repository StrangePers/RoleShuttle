"""Validate repository-relative file scopes."""
from pathlib import Path, PureWindowsPath


def relative_path(value, *, directory=False):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("paths must be nonempty repository-relative strings")
    path = value.replace("\\", "/")
    if path.startswith("/") or PureWindowsPath(path).drive:
        raise ValueError("absolute paths are forbidden in allowed/changed paths")
    parts = path.rstrip("/").split("/")
    if any(p in {"", ".", ".."} for p in parts) or any(c in path for c in "*?[]:"):
        raise ValueError("use literal paths, or a trailing slash for a directory; no traversal/globs")
    return "/".join(parts) + ("/" if directory and path.endswith("/") else "")


def safe_path(value, *, directory=False):
    result = relative_path(value, directory=directory)
    if any(ord(c) < 32 for c in result) or any(p.lower() == ".git" for p in result.split("/")):
        raise ValueError("Git internals and control characters are forbidden")
    return result


def check_scope(repository, changed_paths, allowed_paths, *, directories=False):
    root = Path(repository).resolve()
    allowed = [relative_path(p, directory=True) for p in allowed_paths]
    errors = []
    for value in changed_paths:
        try:
            path = relative_path(value, directory=directories)
            if not (root / path).resolve().is_relative_to(root):
                raise ValueError("symlink escapes repository")
            child = root
            for part in path.rstrip("/").split("/"):
                child = child / part
                if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                    raise ValueError("changed path traverses a symlink/junction")
            if not any(path == rule or rule.endswith("/") and path.startswith(rule) for rule in allowed):
                raise ValueError("outside allowed_paths")
        except (ValueError, OSError, RuntimeError) as exc:
            errors.append(f"{value}: {exc}")
    return errors
