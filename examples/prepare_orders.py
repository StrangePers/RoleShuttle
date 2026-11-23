"""Create a new local Git repository for the orders example."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess


def prepare(target):
    seed = Path(__file__).resolve().parent / "orders"
    if target.resolve().is_relative_to(seed):
        raise ValueError("Choose a directory outside the example source")
    target.mkdir()  # A fresh directory protects existing work.
    for name in ("AGENTS.md", "checks.py", "options.json", "orders.py", "policy/catalog.md",
                 "policy/release.md", "policy/amber.md", "policy/draft.md", "policy/archive.md"):
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed / name, dest)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    for role in ("AUTHOR", "COMMITTER"):
        env.update({f"GIT_{role}_NAME": "Orders Example", f"GIT_{role}_EMAIL": "orders@example.invalid",
                    f"GIT_{role}_DATE": "2001-01-01T00:00:00Z"})
    def git(*args):
        return subprocess.check_output(["git", "-c", "core.autocrlf=input", "-c", "commit.gpgSign=false",
                                        "-c", "core.hooksPath=.git/unused-hooks", "-C", str(target),
                                        *args], env=env, text=True).strip()
    git("init", "--quiet", "--initial-branch=main", "--object-format=sha1", "--template=")
    git("add", "--", ".")
    git("commit", "-qm", "Seed fictional order export")
    return git("rev-parse", "HEAD")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_directory", type=Path)
    parser.add_argument("--with-local-profile", action="store_true",
                        help="Copy the demo worker profile into a Git-ignored project directory")
    args = parser.parse_args()
    print(prepare(args.new_directory))
    local = args.new_directory / ".roleshuttle"
    local.mkdir()
    (local / ".gitignore").write_text("*\n", encoding="utf-8")
    if args.with_local_profile:
        shutil.copyfile(Path(__file__).with_name("local-roles.json"), local / "roles.json")
