# Template validation

Verified on an Ubuntu systemd/cgroup-v2 server on 2026-11-17, in fresh isolated instances with new service users, paths, ports and executor HOME. Existing site services and account data were preserved. Target-specific reports, screenshots and credentials are private and not included.

Passed:

- Skill structure validation; Python source compilation; syntax checks for 63 JavaScript/module files.
- Seven package boundary regressions: exact source manifests, traversal/shared roots, symlink ancestors, occupied port, credential canaries, package hygiene and tampered/traversing manifests.
- Fresh installation of the full locked Python dependency set in a separate empty environment; required imports succeed.
- Fresh isolated first-install service startup, including a second instance with the optional private server browser; no production overwrite.
- 51 API checks: public login/static resources, unauthorized API/page rejection, wrong/correct password, administrator/ordinary-user controls, duplicate users, logout/session invalidation.
- Independent Chromium acceptance: actual login/title/logo/light appearance; honest unconfigured-account state; directory creation on actual disk; PNG thumbnail, filename, full preview and Escape; uploaded draft refresh; project label/collapse persistence; ownership and traversal restrictions; no browser script errors.
- Restart of the new gateway preserved the ordinary test account and independent worker process. Executor identity, private directory modes and cgroup delegation were checked.
- Optional browser rendered an actual RFB desktop; administrator relay and dedicated Unix sockets worked, no TCP listener existed in that browser service cgroup, the profile was fresh and Chrome sandbox remained enabled. No third-party login or challenge was completed.
- Private exact-identifier review and public package hygiene scan reported no personal-source identifiers or blocked files. Normal vendor notices remain intact.
- The external UXWing icon download was independently fetched from its original HTTPS source and matched the installation digest. It is not distributed in the source package.

Not claimed:

- No fresh Claude account was supplied to these isolated instances. Real inference, populated AI history restoration, account-specific models, provider quota, live stop and active-session steering were not revalidated by this packaging task. They are mandatory after new-server authorization before reporting a fully working provider deployment.
- Public DNS/certificate issuance was not exercised for these loopback-only test instances. Verify it on the new target before public release.
- The first-install path was exercised with an explicitly selected compatible runtime; a separate clean environment verified lock installation. A fresh target needs its Python venv prerequisite before the installer can create its own runtime.
- Optional quota-probe provider compatibility is not guaranteed; missing/incompatible official components produce an unknown/unavailable result.

Reproduce server-side package checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 skills/claude-workbench-deploy/scripts/privacy_scan.py
```

Run `verify.py` only against an explicitly authorized test target; it creates a disposable ordinary user. Keep its optional credential-bearing report outside the repository.
