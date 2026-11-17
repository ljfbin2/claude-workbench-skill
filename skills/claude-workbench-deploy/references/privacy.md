# Privacy before publication

Publish a fresh allowlisted source tree, never the original Git history or a recursive copy of a live deployment. Exclude account databases, events, histories, workspaces, uploads, browser profiles/cookies, SSH material, authorization codes, .env/environment files, logs, screenshots, HAR files, local paths, personal hostnames/IPs and original reports. Do not include the proprietary Claude executable, venv or dependency caches.

`privacy_scan.py` checks blocked files, symlinks, key/token shapes, personal Windows path shapes and global IPv4 addresses; it reports only file paths and rule names, never matching secret text. Also run a separate private exact-identifier scan for the source owner's identifiers. Third-party license emails/URLs may be required attribution; allow them narrowly in vendor files rather than deleting license notices. Review Git author/committer metadata as well as file contents. GitHub account ownership itself is public when using a public repository.

Create release manifests only from regular allowlisted text source. Keep generated reports and deployment secrets outside the repository. A `.gitignore` is not an export policy. Never first commit a secret and then delete it: it remains in history. If credentials were disclosed, remove publication and rotate through the authorized provider flow.
