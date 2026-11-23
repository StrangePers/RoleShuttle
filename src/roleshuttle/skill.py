"""Install the packaged controller instructions in a single project's skill directory."""
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory


CLIENT_DIRS = {"codex": ".agents", "claude-code": ".claude", "opencode": ".opencode"}


def install(client, project):
    root = Path(project).resolve()
    parent = root / CLIENT_DIRS[client] / "skills"
    target = parent / "roleshuttle"
    skill = target / "SKILL.md"
    if not skill.resolve().is_relative_to(root):
        raise ValueError("The skill path points outside this project")

    content = files("roleshuttle").joinpath("SKILL.md").read_text(encoding="utf-8")
    parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Publish the complete directory at once. Readers never see a partial SKILL.md.
        with TemporaryDirectory(dir=parent) as temp:
            prepared = Path(temp) / "roleshuttle"
            prepared.mkdir()
            (prepared / "SKILL.md").write_text(content, encoding="utf-8", newline="\n")
            try:
                prepared.rename(target)
                return skill
            except OSError:
                if not target.exists():
                    raise
    if skill.is_file() and skill.read_bytes().replace(b"\r\n", b"\n") == content.encode("utf-8"):
        return skill
    raise ValueError(f"Existing skill differs at {target}. Compare it with 'roleshuttle skill' before replacing it.")
