"""Pinned semgrep rules: the same code always meets the same rules.

    config   print the semgrep arguments for the rules in deploy/semgrep/rules.lock.json.
             On a cold cache it downloads the semgrep-rules tarball at the pinned commit,
             checks its SHA-256 and extracts the listed files; later runs are offline.
    refresh  pin the current p/python and p/owasp-top-ten rule sets: map every registry
             rule id to its file at a semgrep-rules commit (latest develop by default)
             and rewrite the lock.

The rule text is never committed: its licence allows use but not redistribution, and
this repository is public. Each extracted rule gets the registry's id back, and semgrep
runs with --no-rewrite-rule-ids, so ids in nosemgrep comments match the registry's.
Standard library only.
    python3 tools/semgrep_rules.py config
    python3 tools/semgrep_rules.py refresh [--commit <sha>]
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "deploy" / "semgrep" / "rules.lock.json"
RULESETS = ("python", "owasp-top-ten")
REGISTRY_URL = "https://semgrep.dev/c/p/{}"
TARBALL_URL = "https://codeload.github.com/semgrep/semgrep-rules/tar.gz/{}"
LATEST_URL = "https://api.github.com/repos/semgrep/semgrep-rules/commits/develop"
ID_LINE = re.compile(r"^(\s*)- id:\s*(\S+)", re.M)

Fetch = Callable[[str], bytes]


class RulesError(Exception):
    """A one-line reason the pinned rules cannot be used."""


def fetch(url: str) -> bytes:
    """GET one of the fixed https URLs above."""
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310  # nosec B310: fixed https URLs
        body: bytes = response.read()
    return body


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "casino-dashboard" / "semgrep-rules"


def rule_ids(text: str) -> list[str]:
    """Ids of the top-level rules in one rules file; nested `- id:` lines are not rules."""
    matches = ID_LINE.findall(text)
    if not matches:
        return []
    indent = min(len(lead) for lead, _ in matches)
    return [rule_id.strip("'\"") for lead, rule_id in matches if len(lead) == indent]


def qualify(path: str, rule_id: str) -> str:
    """The registry id of a rule: its file path, dotted, then its own id."""
    return ".".join(Path(path).with_suffix("").parts) + "." + rule_id


def tar_rule_files(tarball: bytes) -> dict[str, str]:
    """Every rules file in the tarball, keyed by its path inside the repository."""
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = member.name.split("/", 1)[-1]
            if not member.isfile() or not path.endswith((".yaml", ".yml")):
                continue
            if path.startswith((".", "stats/")) or ".test." in path:
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                files[path] = handle.read().decode("utf-8", errors="replace")
    return files


def build_lock(
    commit: str, tarball: bytes, rulesets: dict[str, str], today: str
) -> tuple[dict[str, Any], list[str]]:
    """The lock for these rule sets at this commit, and the registry ids with no file."""
    wanted = sorted({rule_id for text in rulesets.values() for rule_id in rule_ids(text)})
    index: dict[str, str] = {}
    for path, text in tar_rule_files(tarball).items():
        for rule_id in rule_ids(text):
            index[qualify(path, rule_id).lower()] = path
    rules = [rule_id for rule_id in wanted if rule_id.lower() in index]
    lock = {
        "date": today,
        "commit": commit,
        "sha256": hashlib.sha256(tarball).hexdigest(),
        "rulesets": [f"p/{name}" for name in rulesets],
        "files": sorted({index[rule_id.lower()] for rule_id in rules}),
        "rules": rules,
    }
    return lock, [rule_id for rule_id in wanted if rule_id.lower() not in index]


def pin_ids(path: str, text: str, rules: dict[str, str]) -> str:
    """The file with each rule's id set to its registry id (its qualified id if unpinned)."""
    indent = min((len(lead) for lead, _ in ID_LINE.findall(text)), default=0)

    def rename(match: re.Match[str]) -> str:
        if len(match.group(1)) != indent:
            return match.group(0)
        full = qualify(path, match.group(2).strip("'\""))
        return match.group(0)[: match.start(2) - match.start(0)] + rules.get(full.lower(), full)

    return ID_LINE.sub(rename, text)


def materialize(lock: dict[str, Any], root: Path, get: Fetch) -> tuple[Path, list[str]]:
    """The directory holding the pinned rules, and the rule ids semgrep must skip.

    The directory is named after the lock's hash, so any change to the lock starts a fresh
    one. It is built in a temporary sibling and renamed into place, so a failed download
    or hash check never leaves a half-filled cache behind.
    """
    for path in lock["files"]:
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise RulesError(f"semgrep rules: unsafe path in the lock: {path}")
    key = hashlib.sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()[:16]
    target = root / key
    rules = {rule_id.lower(): rule_id for rule_id in lock["rules"]}
    if not target.is_dir():
        tarball = get(TARBALL_URL.format(lock["commit"]))
        if hashlib.sha256(tarball).hexdigest() != lock["sha256"]:
            raise RulesError(f"semgrep rules: SHA-256 mismatch for commit {lock['commit']}")
        available = tar_rule_files(tarball)
        missing = [path for path in lock["files"] if path not in available]
        if missing:
            raise RulesError(f"semgrep rules: {missing[0]} is not in commit {lock['commit']}")
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=root, prefix=".partial-"))
        try:
            for path in lock["files"]:
                out = staging / path
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(pin_ids(path, available[path], rules), encoding="utf-8")
            staging.rename(target)
        except OSError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    skip = {
        rule_id
        for path in lock["files"]
        for rule_id in rule_ids((target / path).read_text(encoding="utf-8"))
        if rule_id.lower() not in rules
    }
    return target, sorted(skip)


def semgrep_args(directory: Path, skip: Sequence[str]) -> list[str]:
    args = ["--config", str(directory), "--no-rewrite-rule-ids"]
    for rule_id in skip:
        args += ["--exclude-rule", rule_id]
    return args


def load_lock(path: Path) -> dict[str, Any]:
    try:
        lock: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RulesError(f"semgrep rules: cannot read {path}: {exc}") from exc
    return lock


def latest_commit(get: Fetch) -> str:
    commit: str = json.loads(get(LATEST_URL))["sha"]
    return commit


def refresh(lock_path: Path, commit: str | None, get: Fetch) -> list[str]:
    """Rewrite the lock from the live registry; returns the registry ids left unmapped."""
    commit = commit or latest_commit(get)
    rulesets = {name: get(REGISTRY_URL.format(name)).decode("utf-8") for name in RULESETS}
    today = datetime.now(UTC).date().isoformat()
    lock, unmapped = build_lock(commit, get(TARBALL_URL.format(commit)), rulesets, today)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return unmapped


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="print the semgrep arguments for the pinned rules")
    refresh_cmd = sub.add_parser("refresh", help="pin the current registry rule sets")
    refresh_cmd.add_argument("--commit", help="semgrep-rules commit (default: latest develop)")
    args = parser.parse_args(argv)
    try:
        if args.command == "config":
            directory, skip = materialize(load_lock(LOCK), cache_root(), fetch)
            print(" ".join(semgrep_args(directory, skip)))
            return 0
        unmapped = refresh(LOCK, args.commit, fetch)
    except RulesError as exc:
        print(exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"semgrep rules: download or cache failed: {exc}", file=sys.stderr)
        return 1
    lock = load_lock(LOCK)
    print(f"pinned {len(lock['rules'])} rules in {len(lock['files'])} files at {lock['commit']}")
    for rule_id in unmapped:
        print(f"not in the repository, skipped: {rule_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
