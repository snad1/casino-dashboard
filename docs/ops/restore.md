# Backups and restore

Every night at 03:30 (host time, up to 15 minutes later), `deploy/backup.sh run` dumps the dashboard database. That is users, roles, sessions, audit log, alerts, comments and the bet history copy. It runs `pg_dump -Fc`, and the dump streams through `age` to your public key, so no readable copy of the database ever lands on the server's disk. The file goes into `/var/backups/casino-dashboard` and is copied to your rclone remote. Copies older than 14 days are deleted in both places.

Only you can read the backups: the age private key stays on your own machine. The bots' SQLite databases are not part of this; they stay with `wolfctl archives`.

## One-time setup

1. **On your Mac**, make the key pair and keep the private key safe (password manager + one offline copy):

   ```bash
   brew install age
   age-keygen -o ~/casino-backup-key.txt     # prints "Public key: age1..."
   ```

   Without this file no backup can ever be restored. Don't put it on the VPS.

2. **On the VPS** (root), set up the rclone remote, for example Backblaze B2, S3 or a Storage Box over SFTP. The credentials stay in rclone's own config (`/root/.config/rclone/rclone.conf`), never in the repo:

   ```bash
   apt-get install -y rclone age
   rclone config                              # create a remote, e.g. "b2"
   rclone mkdir b2:casino-backups/wolfbet
   ```

3. Point the backup at your public key and the remote, then turn the timer on:

   ```bash
   cd ~/casino-dashboard
   deploy/backup.sh configure --recipient age1... --remote b2:casino-backups/wolfbet
   deploy/backup.sh install
   systemctl start casino-backup.service      # first backup now
   journalctl -u casino-backup -n 5           # "backup ok: dashboard-<UTC>.dump.age <size> bytes"
   rclone ls b2:casino-backups/wolfbet
   ```

`configure` writes `~/.config/casino-dashboard/backup.env` (mode 600). It holds only the public key and the remote name. The timer runs with `Nice=10` and idle IO priority, so it yields to the bots.

## When a backup fails

`journalctl -u casino-backup` shows `backup failed: <why>`:

- **pg_dump or age did not finish:** nothing was kept. Check that the stack is up (`wolfctl dash status`), then run `systemctl start casino-backup.service`.
- **copy failed:** the local file is kept. Fix the remote (`rclone ls <remote>`) and run the backup again; the next run copies the new file.

## Restore after losing the server

1. Set up the new VPS as in `deploy/README.md`: run `install.sh`, create the Coolify resource and deploy. The fresh deploy creates an empty database with all the tables and roles.
2. Fetch the newest backup and bring the key over for the duration of the restore:

   ```bash
   rclone copy b2:casino-backups/wolfbet/dashboard-<newest>.dump.age /root/restore/
   scp ~/casino-backup-key.txt root@<vps>:/root/restore/key.txt     # from your Mac
   ```

3. Restore. This stops api, ingest and mailer, replaces their data and starts them again:

   ```bash
   deploy/restore.sh into /root/restore/dashboard-<newest>.dump.age \
       --identity /root/restore/key.txt --yes-replace-dashboard-data
   shred -u /root/restore/key.txt
   ```

4. Check: sign in as the owner, open "Audit log" and "Status", and look at the row counts the script printed.

## Restore drill

Run this once a quarter. It proves a backup can be read and restored without touching anything live. It restores into a throwaway Timescale container, prints row counts and timings, and removes the container:

```bash
deploy/restore.sh drill /var/backups/casino-dashboard/dashboard-<newest>.dump.age \
    --identity /root/restore/key.txt
shred -u /root/restore/key.txt
```

| Date | Where | Backup size | Rows (users / audit / sessions / bets) | Start | Decrypt + restore | Total |
|---|---|---|---|---|---|---|
| 2026-11-20 | MacBook, e2e stack's seeded data (`age` and `rclone` from a container) | 108 KiB | 1 / 0 / 6 / 1440, migration 0013, all matching the source | 1 s | 1 s | 2 s (backup itself 2 s) |

The first real-data drill on the VPS goes in the next row. `pg_dump` warns about circular foreign keys on `continuous_agg`; that is Timescale's own catalog and does not affect a full dump and restore.
