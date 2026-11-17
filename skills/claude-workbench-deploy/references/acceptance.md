# Acceptance

Record server time, OS/architecture, runtime/CLI/SDK versions, source manifest hash and which checks actually ran. Keep target-specific details and raw reports outside the public source tree. Never report fixture checks as a live provider result.

## Automated and API checks

1. Run privacy scan; compile Python without leaving caches in the package, check first-party JS syntax and local static/import closure.
2. Test deploy plan collision rejection: existing directory/user/unit, occupied loopback/public port, path traversal/symlink, protected-HOME executable. Run `--apply` only with new unique paths/identities/ports on the authorized server.
3. Run `verify.py` with an explicit loopback or HTTPS base URL, administrator name and private password file. It verifies unauthenticated protection, incorrect/correct password, core APIs, admin-only user creation, a fresh ordinary user login and denied management/resource/browser access, logout invalidation, title/favicon and local static resources.
4. Restart only the new gateway. Verify the created test user still logs in and prior test history is retained. Verify the separate worker PID/task survives gateway restart. Restarting the executor is allowed only with no important active tasks; an interrupted task must not silently be reported as successful.

## Real browser checks

Use a real desktop browser on the server. Check the login page, protected navigation, correct title/logo/favicon, light theme, settings, session creation, search, rename/delete/pin, directory creation and project sidebar. Upload a tiny PNG, check thumbnail/filename and full-size preview; verify another user cannot read the file or session. Verify path traversal is blocked. Preserve drafts on send failure.

After a fresh Claude account is authorized: ask one harmless actual question, edit a disposable file inside the new workspace and inspect the actual file contents; expand execution events; stop a controlled long-running task; send follow-up/confirmation in the same active session; refresh/reconnect and continue history. Test another session without creating a second writer for the first session. Verify real activity timestamps and unknown/stale/error states rather than a permanent fake spinner. Do not run trading, purchases or unrelated deployment tasks for acceptance.

If using a deterministic test CLI/protocol fixture for reproducible restart/stream checks, keep it outside runtime assets and label every test explicitly. Never expose it as a configured provider or use it to assert actual model access. If no fresh account is supplied, state that real inference, provider quota and model access are unverified.

## Public entry

Verify DNS, certificate chain, HTTP-to-HTTPS behavior, secure/HttpOnly session cookie, anonymous/ordinary-user route restrictions, internal ports not publicly reachable, service enablement and log rotation. A 200 response alone is not browser acceptance. Report any omitted check and the reason.
