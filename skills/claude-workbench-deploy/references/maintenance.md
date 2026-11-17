# Maintenance, backup and rollback

## Preserve boundaries

Read private `/etc/<instance>/installation.json` and actual running unit metadata before acting. Back up before a release. Never initialize an existing data directory with a new administrator password. Do not reuse another instance's database, login HOME, tokens or session secret. The installer is first-install only; upgrades need a separate reviewed code-only plan.

## Backups

Credentials, user hashes, histories, files and browser profiles are private. Store encrypted backups outside the repo with restricted access. For a consistent complete snapshot, arrange a maintenance window, finish/stop tasks with the user's agreement, stop only the relevant new instance, and copy its config/data with ownership/modes preserved. Restart the same units and verify. Alternatively use SQLite's online backup API for each database; copying an open `.sqlite3` alone without WAL is not a consistent backup. A database-only backup does not include uploads or CLI histories.

Record a manifest and restore procedure privately. Test restore into distinct paths/ports/users, never over a live instance. Do not upload backups to public GitHub or attach them to issues.

## Releases and rollback

Keep the last known-working source/runtime and its digest. Review dependency/model/protocol changes explicitly. Preserve configuration/data across code releases. Restarting the web gateway must leave the independent executor alone. A change to the executor or its cgroup provenance requires a safe task-free window and full recovery checks.

If a source-only release fails without schema migration, restore the previous code/runtime and restart only the affected instance services. If a database migration occurred, stop and follow its reviewed restore plan; do not blindly roll back source against a newer schema or overwrite later user messages. Recheck login, events, files and actual task states.

## Logs and recovery

Use `systemctl status <instance>-web <instance>-worker` and bounded journal metadata. Inspect errors locally, redact before sharing. Do not dump configuration, passwords, authorization URLs/codes, cookies or prompts. systemd restarts failed daemons; unknown/interrupted tasks require explicit reconciliation and must not be automatically replayed because duplicate execution can have side effects. Check real process/cgroup provenance before signaling anything. Never use a broad process-name kill.

Optional browser profiles retain personal sessions; restrict them like credentials. Quota cache can become stale or unavailable; preserve timestamps and unknown labels. No subscription, region, account-standing or availability guarantee is made.
