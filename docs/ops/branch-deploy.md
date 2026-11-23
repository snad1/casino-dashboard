# Running a branch on the test server

The live server and the dashboard's Coolify app run `main`. To try a feature branch first, run it on a test server: each server keeps its own checkout, so one never affects the other. `<branch>` below is that branch's name.

## Switch the test server to the branch

Run on the test server, in the bot's repo checkout (`~/wolfbet` for WolfBet):

```bash
cd ~/wolfbet
wolfctl update --branch <branch>
wolfctl status                     # "Code: <branch> @ <sha>, N ahead of origin/main"
wolfctl web status
```

`wolfctl update --branch` refuses a checkout with local changes (exit 4, and it lists them), then fetches `origin`, switches to the branch (creating it to track `origin/<branch>` the first time) and fast-forwards. It never resets or forces. It then runs the normal update: copies the files into `~/wolfbet-bot`, reinstalls dependencies (including `casino-shared` from the ref in `requirements.txt`, `main` unless the branch changes it) and restarts the running units.

## Everyday loop on the branch

```bash
cd ~/wolfbet && wolfctl update
```

## If the branch was rewritten

A force-push (a rebase onto `main`, or history cleanup) leaves a checkout that is already on the branch unable to `git pull --ff-only`, because its local commits are no longer in the remote; `wolfctl update --branch` then stops with exit 1 and leaves the checkout as it was. Reset it once, by hand:

```bash
cd ~/wolfbet
git status --short                 # must be empty; commit or stash anything real first
git fetch origin
git reset --hard origin/<branch>
wolfctl update
```

`reset --hard` discards tracked changes in that checkout. Untracked files stay; `git clean -nd` shows what they are before you remove anything. Never run this on the live server's checkout.

## Back to live code

```bash
cd ~/wolfbet
wolfctl update --branch main
```

## Live server

Nothing to do: it stays on `main`, and `git pull --ff-only` there cannot pick up a branch. Never run `git switch` on the live server's checkout unless you mean to change what live runs.

## Other services

The same steps apply in `~/goated`, `~/shuffle`, `~/stakedotcom`, `~/cryptogames`, `~/duckdice` with their own ctl scripts (`goatedctl`, `shufflectl`, `stakectl`, `cgctl`, `duckctl`) and `.env` names once their security stories land on the branch.
