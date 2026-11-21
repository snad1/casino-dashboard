# Security scans

`make check` runs lint, types, tests and `make security`. CI (`.github/workflows/ci.yml`) runs the same Makefile targets on every push to `main` and `feat/casino-dashboard` and on pull requests to `main`. A finding at or above the threshold fails the build.

## What runs

| Target | Tool | Scope | Fails on |
|---|---|---|---|
| `make bandit` | bandit 1.9.4 | `backend/app`, `agent/agent` | medium or high severity |
| `make pip-audit` | pip-audit 2.10.1 | runtime deps from `backend/uv.lock`, `agent/uv.lock` | any known vulnerability |
| `make pnpm-audit` | pnpm audit | frontend production deps | high or critical |
| `make semgrep` | semgrep 1.185.0, `p/python` + `p/owasp-top-ten` pinned in `deploy/semgrep/rules.lock.json` | `backend/app`, `agent/agent`, `frontend/src` | any finding |
| `make gitleaks` | gitleaks 8.30.1 | full git history, then the working tree | any secret |
| `make hadolint` | hadolint 2.15.1 | `backend/Dockerfile` | warning or error |
| `make image-scan` | trivy 0.76.2 | the built api image | high or critical with a fix available |

Python tools run with `uvx <tool>==<version>`, outside the app environments. gitleaks, hadolint and trivy are binaries: `brew install gitleaks hadolint trivy` locally; CI downloads them and checks their SHA-256. `image-scan` builds the image, so Docker must be running.

The git hook also runs `.pre-commit-config.yaml` (gitleaks on staged changes, ruff, prettier) after the kit's rule scan. Do not run `pre-commit install`; it would replace that hook.

## Refreshing the semgrep rules

The semgrep rules are pinned so that a scan only fails when the code changes. The live registry adds rules, and an unpinned scan once failed a review on agent code nobody had touched. `deploy/semgrep/README.md` gives the pinned date and commit. The first `make semgrep` downloads that commit's tarball once and checks its SHA-256. A hash mismatch stops the scan with `semgrep rules: SHA-256 mismatch`; refresh the lock if that happens.

To take newer rules:

1. Run `make semgrep-rules`. It pins the latest `develop` commit of semgrep-rules to today's `p/python` and `p/owasp-top-ten`, then rewrites `deploy/semgrep/rules.lock.json`. It also lists any registry rule it could not find in that commit. Those rules are skipped.
2. Run `make semgrep`. Fix each new finding, or suppress it on its line as described below.
3. Update the date and commit in `deploy/semgrep/README.md`, then commit the lock in the same change as the fixes.

## Suppressing a finding

Fix the finding when you can. When it does not apply, suppress that one finding, never the rule for the whole repo, and always say why on the same line or entry. A suppression without a reason is rejected in review.

| Tool | How |
|---|---|
| bandit | `# nosec B108: <why it is safe here>` on the flagged line |
| semgrep | `# nosemgrep: <rule-id>  <why>` on the flagged line (`// nosemgrep` in TypeScript) |
| gitleaks | test fixtures only: `gitleaks:allow` in a comment on the line, with the reason; a real secret is rotated, never allowed |
| hadolint | `# hadolint ignore=DL3008 <why>` on the line before the instruction |
| trivy | `.trivyignore` at the repo root: the CVE id, a comment with the reason and a review date |
| pip-audit | `--ignore-vuln <ID>` in the Makefile target, with a comment holding the reason and a review date |
| pnpm audit | `pnpm.auditConfig.ignoreCves` in `frontend/package.json`, with the reason in the pull request and a review date |

Every suppression with a review date is re-checked on that date; remove it once a fixed version is available.
