# Running a branch on the test server

The live server pulls `main`. The test server pulls `feat/casino-dashboard` until that branch is proven. Each server keeps its own checkout, so one never affects the other.

## Switch the test server to the branch

Run on the test server, in the bot's repo checkout (`~/wolfbet` for WolfBet):

```bash
cd ~/wolfbet
git status --short                 # must be empty; stop and inspect if it is not
git fetch origin
git switch -c feat/casino-dashboard --track origin/feat/casino-dashboard
git pull --ff-only
```

Then refresh the install and restart:

```bash
wolfctl update
wolfctl status
wolfctl web status
```

`wolfctl update` pulls the branch the checkout is on, copies the files into `~/wolfbet-bot`, reinstalls dependencies (including `casino-shared` from its `feat/casino-dashboard` ref) and restarts the running units.

## First switch: required web credentials

The web admin refuses to start with the shipped defaults. Before `wolfctl update` restarts `wolfbet-web`, set both values in `~/wolfbet-bot/.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # WOLFBET_WEB_SECRET_KEY, 32+ bytes
# WOLFBET_WEB_ADMIN_PASS: 12+ characters, your own
```

A database that was created with the old default password is repaired on the next start: the stored `changeme123` hash is replaced with the password from `.env`. A password you already chose is never overwritten.

## Everyday loop on the branch

```bash
cd ~/wolfbet && git pull --ff-only && wolfctl update
```

## Back to live code

```bash
cd ~/wolfbet
git switch main
git pull --ff-only
wolfctl update
```

## Live server

Nothing to do: it stays on `main`, and `git pull --ff-only` there cannot pick up a branch. Never run `git switch` on the live server's checkout unless you mean to change what live runs.

## Other services

The same steps apply in `~/goated`, `~/shuffle`, `~/stakedotcom`, `~/cryptogames`, `~/duckdice` with their own ctl scripts (`goatedctl`, `shufflectl`, `stakectl`, `cgctl`, `duckctl`) and `.env` names once their security stories land on the branch.
