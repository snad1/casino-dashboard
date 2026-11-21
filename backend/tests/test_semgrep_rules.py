"""tools/semgrep_rules.py: the lock maps registry ids to pinned files, and scans stay offline.

The tarball is built in memory and the fetcher is a fake, so no test touches the network.
"""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
COMMIT = "c0ffee"

PICKLE = """rules:
  - id: avoid-pickle
    message: pickle
    languages: [python]
    severity: WARNING
    pattern: pickle.loads(...)
  - id: avoid-dill
    message: dill
    languages: [python]
    severity: WARNING
    pattern: dill.loads(...)
"""
X509 = """rules:
- id: X509-subject-name-validation
  message: x509
  languages: [csharp]
  severity: WARNING
  metadata:
    references:
      - id: not-a-rule
  pattern: X509(...)
"""
REGISTRY = """rules:
- id: python.lang.security.deserialization.pickle.avoid-pickle
- id: csharp.lang.security.cryptography.x509-subject-name-validation.x509-subject-name-validation
- id: go.lang.security.gone.gone
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("semgrep_rules", TOOLS / "semgrep_rules.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load()


def _tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"semgrep-rules-{COMMIT}/{path}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


TARBALL = _tarball(
    {
        "python/lang/security/deserialization/pickle.yaml": PICKLE,
        "csharp/lang/security/cryptography/x509-subject-name-validation.yaml": X509,
        "python/lang/security/deserialization/pickle.test.yaml": PICKLE,
        ".github/workflows/ci.yml": "rules: []\n",
    }
)


class Fetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.body


def _offline(url: str) -> bytes:
    raise AssertionError(f"network used for {url}")


def _lock() -> dict[str, Any]:
    lock, _ = rules.build_lock(COMMIT, TARBALL, {"python": REGISTRY}, "2026-11-21")
    return dict(lock)


def test_rule_ids_reads_only_top_level_rules() -> None:
    assert rules.rule_ids(X509) == ["X509-subject-name-validation"]
    assert rules.rule_ids(PICKLE) == ["avoid-pickle", "avoid-dill"]


def test_lock_maps_registry_ids_to_files_and_reports_the_rest() -> None:
    lock, unmapped = rules.build_lock(COMMIT, TARBALL, {"python": REGISTRY}, "2026-11-21")

    assert lock["commit"] == COMMIT
    assert lock["date"] == "2026-11-21"
    assert lock["sha256"] == hashlib.sha256(TARBALL).hexdigest()
    assert lock["rulesets"] == ["p/python"]
    assert lock["files"] == [
        "csharp/lang/security/cryptography/x509-subject-name-validation.yaml",
        "python/lang/security/deserialization/pickle.yaml",
    ]
    assert len(lock["rules"]) == 2
    assert unmapped == ["go.lang.security.gone.gone"]


def test_cold_cache_downloads_once_and_gives_rules_their_registry_ids(tmp_path: Path) -> None:
    fetcher = Fetcher(TARBALL)

    directory, skip = rules.materialize(_lock(), tmp_path, fetcher)

    assert fetcher.urls == [rules.TARBALL_URL.format(COMMIT)]
    pickle = (directory / "python/lang/security/deserialization/pickle.yaml").read_text()
    assert "- id: python.lang.security.deserialization.pickle.avoid-pickle\n" in pickle
    x509 = directory / "csharp/lang/security/cryptography/x509-subject-name-validation.yaml"
    assert "csharp.lang.security.cryptography.x509-subject-name-validation." in x509.read_text()
    assert "- id: not-a-rule" in x509.read_text()
    assert skip == ["python.lang.security.deserialization.pickle.avoid-dill"]


def test_warm_cache_never_touches_the_network_and_gives_the_same_answer(tmp_path: Path) -> None:
    first = rules.materialize(_lock(), tmp_path, Fetcher(TARBALL))

    assert rules.materialize(_lock(), tmp_path, _offline) == first


def test_hash_mismatch_stops_and_leaves_no_cache(tmp_path: Path) -> None:
    lock = _lock()
    lock["sha256"] = "0" * 64

    cache = tmp_path / "cache"

    with pytest.raises(rules.RulesError, match="SHA-256 mismatch"):
        rules.materialize(lock, cache, Fetcher(TARBALL))
    assert not cache.exists()


def test_a_path_outside_the_cache_is_refused(tmp_path: Path) -> None:
    lock = _lock()
    lock["files"] = ["../../escape.yaml"]

    with pytest.raises(rules.RulesError, match="unsafe path"):
        rules.materialize(lock, tmp_path, _offline)


def test_a_file_missing_from_the_commit_is_refused(tmp_path: Path) -> None:
    lock = _lock()
    lock["files"] = [*lock["files"], "python/gone.yaml"]

    with pytest.raises(rules.RulesError, match=r"python/gone.yaml is not in commit"):
        rules.materialize(lock, tmp_path, Fetcher(TARBALL))


def test_semgrep_args_keep_registry_ids_and_skip_unpinned_rules() -> None:
    assert rules.semgrep_args(Path("/cache/x"), ["a.b"]) == [
        "--config",
        "/cache/x",
        "--no-rewrite-rule-ids",
        "--exclude-rule",
        "a.b",
    ]


def test_refresh_writes_the_lock(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        if url == rules.LATEST_URL:
            return json.dumps({"sha": COMMIT}).encode()
        if url.startswith("https://semgrep.dev/"):
            return REGISTRY.encode()
        return TARBALL

    lock_path = tmp_path / "rules.lock.json"
    unmapped = rules.refresh(lock_path, None, fetch)

    lock = json.loads(lock_path.read_text())
    assert lock["commit"] == COMMIT
    assert lock["rulesets"] == ["p/python", "p/owasp-top-ten"]
    assert unmapped == ["go.lang.security.gone.gone"]


def test_config_without_a_lock_fails_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rules, "LOCK", tmp_path / "missing.json")

    assert rules.main(["config"]) == 1
    assert capsys.readouterr().err.startswith("semgrep rules: cannot read")


def test_committed_lock_is_complete() -> None:
    lock = rules.load_lock(rules.LOCK)

    assert set(lock) == {"date", "commit", "sha256", "rulesets", "files", "rules"}
    assert lock["rulesets"] == ["p/python", "p/owasp-top-ten"]
    assert len(lock["sha256"]) == 64
    assert lock["files"] and lock["rules"]
