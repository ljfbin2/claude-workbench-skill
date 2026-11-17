# Claude Workbench Skill

A reusable deployment Skill with a privacy-clean source template for a server-hosted Claude Code web workbench. This is an independent community interface, not an official Anthropic product.

## Use

Install `skills/claude-workbench-deploy` into your coding assistant's skill directory. Then ask:

> Use $claude-workbench-deploy to deploy the bundled workbench on my new Ubuntu server. Inspect first, preserve existing services, and confirm the concrete deployment plan before changes.

Provide the new server and domain through your usual private channel. Administrator password and a **fresh official Claude login** are required; this repository contains neither. Read [the Skill](skills/claude-workbench-deploy/SKILL.md) and [deployment requirements](skills/claude-workbench-deploy/references/deployment.md).

## Included

- Authenticated Python gateway and independent executor, persistent history/events, streaming and reconnect support.
- Session search, rename/delete/pin, work directories, model/effort settings from the installed assistant, optional reviewer/coder workflow.
- Files, image thumbnails/previews, project files and preferences, truthful activity/resource status and on-demand quota details.
- Optional isolated server browser with a fresh profile and authenticated administrator relay.
- A collision-checking first-install script, integrity manifests, acceptance checks and backup/recovery instructions.

## Privacy and scope

Only allowlisted runtime source is included. No real host addresses, domains, administrator identity, passwords, keys, tokens, existing accounts, histories, project contents, uploads, databases, browser profiles or original deployment reports are included. GitHub repository ownership and normal GitHub publication metadata are separate from the template itself.

The installer refuses existing targets; it does not overwrite a website or reset accounts. It is not a one-click subscription purchase or an account migration utility. Model access and quota depend on the target's authorized provider account. An unavailable provider is shown honestly.

Read [acceptance](skills/claude-workbench-deploy/references/acceptance.md), [maintenance](skills/claude-workbench-deploy/references/maintenance.md), [privacy](skills/claude-workbench-deploy/references/privacy.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

The brand icon is retrieved directly from its original source during installation and is not redistributed in this repository. See the notices and deployment reference.
