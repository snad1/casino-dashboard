# semgrep rules

`make semgrep` scans with the `p/python` and `p/owasp-top-ten` registry rule sets as they were on **2026-11-21**. They are pinned to commit `a5ad1714f00c9f7fde980cab0ea1d6dc6d414fd2` of [semgrep/semgrep-rules](https://github.com/semgrep/semgrep-rules), so the same code always gets the same result.

`rules.lock.json` holds the commit, the SHA-256 of its tarball, the rule files and the registry id of every rule. `tools/semgrep_rules.py config` downloads that tarball once into `~/.cache/casino-dashboard/semgrep-rules/` (or `$XDG_CACHE_HOME`) and checks the hash. After that, scans run offline.

The rule text is not committed. The Semgrep Rules License allows using the rules but not redistributing them, and this repository is public.

**Refresh:** run `make semgrep-rules`, then `make semgrep`. Fix any new findings, then commit the lock together with the new date in this file. See `docs/security/scans.md`.
