---
name: claude-workbench-deploy
description: Deploy or reproduce a server-hosted Claude Code web workbench on an Ubuntu systemd server using the bundled privacy-clean source template. Use for fresh installations, deployment planning, authentication setup, verification, backups and recovery. Includes a separate authenticated gateway and executor, durable sessions, files and optional private server browser. Never copies an existing account, credential, history or project.
---

# Claude Code workbench deployment

Work on the target server. Use local tools only as transport. Keep existing sites, users, data and running tasks intact. This is an independent community web interface; it is not an official Claude product.

## Workflow

1. Read `references/deployment.md`. Inspect OS, architecture, Python, systemd/cgroup v2, free disk, running services, listening ports and installed Claude CLI/SDK versions. Do not print credentials or environment files. Prefer existing compatible software; never silently upgrade it.
2. Confirm the target, new instance paths/ports, domain, public exposure, optional server browser, administrator name and minimum scope. Respect any earlier authorization. Collect missing target information together. Passwords, SSH credentials and Claude authorization must use private input; never commit them.
3. Run `python3 scripts/privacy_scan.py` against this package, then run `scripts/deploy.py` without `--apply` to get a collision-checked plan. Inspect the proposed paths and service identities. The installer is first-install only and refuses existing targets. It must not be used to reset an existing installation.
4. With authorization for the concrete plan, install only missing prerequisites from current official sources. Use the locked Python dependencies in `assets/app/requirements-lock.txt` and a compatible official Claude executable readable by the new executor. Never redistribute or copy an account HOME or login. Run the plan again, then `--apply`. See the reference for arguments and loopback/HTTPS boundaries.
5. Complete a fresh official Claude login under the new executor identity and its dedicated HOME. Account availability and supported models are discovered from the actual installed assistant. Do not fabricate configured status, model access, reasoning levels or quota. Keep collaboration disabled until both actual role model identifiers are configured and verified.
6. Run `scripts/verify.py` with the new URL and a private password file. Follow `references/acceptance.md` in a real browser, including a harmless real question and test-file edit once the account is authorized. Fixture-only checks do not count as real model verification. Record unavailable checks explicitly.
7. Before public exposure, verify DNS/HTTPS, unauthenticated/ordinary-user restrictions, restart recovery, file authorization and logs. Do not change an occupied reverse proxy without an explicit integration plan. Deliver the URL, factual results, private backup location and rollback instructions from `references/maintenance.md`.

## Template boundaries

- `assets/app/`: bundled active gateway, executor and frontend source; do not fetch unrelated production code or private deployment directories.
- `assets/browser/desktop.py`: optional server-only private desktop with fresh profile and authenticated relay. Exclude profiles and cookies from publication and backups intended for sharing.
- `assets/*.sha256.json`: integrity manifests; verify before copying. They are checks against accidental drift, not cryptographic signatures.
- `scripts/deploy.py`: default read-only planner; `--apply` creates a fresh isolated installation. It never migrates users, subscriptions or credentials.
- `scripts/verify.py`: login/auth/user persistence checks using disposable accounts and files; requires explicit test target.
- `scripts/privacy_scan.py`: conservative package hygiene checks; also perform a private exact-identifier audit before any publication. A scanner is not proof of complete anonymization.

Preserve bundled working behavior, including session history, streaming, uploads/previews, work-directory creation, session menus, status indicators and project preferences. Do not redesign themes or mobile behavior as part of deployment. New standalone projects follow the user's current desktop/light-theme preferences; changes to this existing template's UI require a separate agreed scope.

## Truthful limits

No promise of 1 ms end-to-end network or model response. Measure UI handling, transport and model time separately. Quota retrieval is provider/version-dependent and on-demand; stale values carry timestamps, missing values stay unknown. The optional quota probe requires explicitly matching installed Node, official JS SDK and CLI versions. See references. Different sessions may run concurrently; same-session input follows the shipped executor and official SDK behavior, never a hidden second writer. High permission modes are real execution privileges and require clear consent.
