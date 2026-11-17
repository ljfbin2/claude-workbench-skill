# Deployment

## Target requirements

Ubuntu with systemd, unified cgroup v2 and Python 3.11+ (3.12 recommended); Git; enough RAM/disk for Claude and uploaded deployment packages. Verify supported platforms against the [current official setup documentation](https://code.claude.com/docs/en/setup) and [Python Agent SDK reference](https://code.claude.com/docs/en/agent-sdk/python). The Python lock file records the template's verified dependency set; test before changing it.

Run the commands on the target server. Inspect `uname -m`, `/etc/os-release`, `python3 --version`, `systemctl`, `/sys/fs/cgroup/cgroup.controllers`, `ss -lntp`, disk/memory and installed CLI version. Never read or print credential files during preflight. Optional apt prerequisites are `python3-venv`, `git`; optional browser needs official Google Chrome, `tigervnc-standalone-server`, `tigervnc-common`, `openbox`, `xauth`, and desktop dependencies. Optional HTTPS needs Caddy installed from its official distribution. Do not upgrade existing software silently.

Install the official CLI using current official instructions. The service executor is a distinct system user with `ProtectHome=yes`; its executable must be in a globally readable, administrator-owned location outside `/root` and `/home`. A user-local CLI installation is not sufficient. The SDK's officially packaged bundled CLI may be used after verifying its provenance, version and file permissions. Do not publish this proprietary binary. The installer tests `--version` as the new executor; this does not import an account.

## Read-only plan

From the skill folder:

```sh
python3 scripts/privacy_scan.py
python3 scripts/deploy.py --instance claude-workbench --claude-bin /opt/claude-cli/claude
```

The default is loopback-only on ports 18752/18751. It checks paths, identities, units and occupied ports, but is not a substitute for firewall and DNS review. A plan does not install anything. Run with root privileges for accurate port/unit checks. Use `--app-dir`, `--data-dir`, `--config-dir`, `--web-port`, `--worker-port` to isolate another instance. Existing targets are refused. Directories must be dedicated, separate and free of symlink ancestors. Existing users, databases, services and credentials must not be removed to make a plan pass.

## Install

After the concrete plan is approved, repeat the same arguments with `sudo python3 scripts/deploy.py --apply ...`. Enter a new administrator password privately, twice. Alternatively use `--password-file /private/path/password` (mode 0600, outside the repository). Do not put a password in an argument, command history or chat. Choose `--admin-user` if a particular administrator name has been privately requested; the public template defaults to `admin`. The one-time plaintext password is removed from the new gateway configuration after the database is initialized; salted scrypt hashes persist in the private database. The installer never resets existing accounts.

New paths default to `/opt/<instance>` (root-owned source), `/etc/<instance>` (0700 private configuration) and `/var/lib/<instance>` (independent users, events, uploads and executor HOME). New units are `<instance>-worker`, `-web`, optionally `-browser`, `-proxy`. Only the worker can write its workspaces/event log. The loopback worker authenticates gateway requests with a random secret. A per-task cgroup is used to stop only that task; do not add wrappers or unit drop-ins without revalidating recovery provenance.

`--runtime-python /opt/compatible-runtime/bin/python` reuses a compatible preinstalled environment for controlled testing; fresh installations normally create their own venv from the lock file. It must be readable/executable by service users and outside protected HOME paths.

## Fresh Claude authorization

Use the new executor identity and configured `WEB_AI_CLAUDE_HOME`; do not run login as root or the gateway user. Start an interactive official CLI login with an explicitly set HOME and fresh XDG directories. Follow its current official flow. Never display or copy refresh tokens. The workbench uses the authorized server-side identity; every website user does not automatically get a separate Claude subscription or filesystem sandbox. Only grant website access to trusted users and understand actual permission modes.

An absent account must remain unconfigured. Verify login with a fresh harmless task, actual file edit and a continued session before claiming model acceptance. No fabricated response may be substituted. CLI/SDK versions must be tested together. Supported models/reasoning levels come from the installed assistant, not the screenshots or a fixed catalog.

## Optional features

- **HTTPS:** `--domain workbench.example.com` sets secure cookies and a dedicated Caddy instance. Confirm DNS and ports 80/443. If occupied, the installer refuses; prepare and approve a narrow vhost integration separately. Do not expose loopback/test HTTP to the internet. Without a domain, use an SSH tunnel for private testing. Domain, firewall and certificate verification remain mandatory before public release.
- **Private server browser:** `--with-browser --browser-display :98 --browser-bin /usr/bin/google-chrome-stable`. Confirm an unused X display. A new system user/profile is created; the VNC/control sockets are Unix-only behind authenticated administrator routes. Never copy a logged-in profile or disable Chrome sandbox for deployment.
- **Collaboration:** configure both `--coder-model ACTUAL_ID --reviewer-model ACTUAL_ID` only after discovering and verifying those candidates on the new account. Default is single model; the role controls validate actual effort support. No model name or account entitlement is promised.
- **Quota probe:** passive task usage and last confirmed values work independently. The optional on-demand experimental probe requires all of `--quota-node`, `--quota-js-sdk`, `--quota-sdk-version`, `--quota-cli-version` to match locally installed official components. Without them the source remains usable and unavailable quota stays unknown. Do not fake a remaining percentage. Do not continuously poll provider quota. Future provider changes may require a reviewed compatibility update.

A failure preserves partial state for inspection. Do not rerun by deleting a live data/config directory. Inspect only metadata-first service logs; never print full environment files, browser cookies, prompts or project files in shared reports.

## External logo asset

The UXWing icon is not bundled because its license prohibits redistribution of the asset. During apply, the installer downloads the exact SVG from its original HTTPS source, verifies its pinned SHA-256, and writes it only into the new website. A network/hash failure stops before creating installation directories; review changed upstream content and license rather than disabling verification. Plan mode does not download anything. The icon is a runtime asset in addition to the bundled source manifest.
