# ARC Origin And Rebrand

## Identity

This system is now **ARC**, short for **Agent Robotic Cluster**.

For project decisions, docs, and future development, treat this repository as the ARC codebase rather than as OpenClaude.

## Source Lineage

ARC is **derived from OpenClaude**.

Upstream source used for the current fork/rebrand work:

- Project: `Gitlawb/openclaude`
- Repository: `https://github.com/Gitlawb/openclaude`
- Baseline commit used during inspection and adaptation: `6af709e65ea61e9071cbccbdcd7c57fe87b0710e`

That provenance is intentionally preserved here so we do not lose architectural context later.

## Why Keep The Source Noted

We explicitly record the OpenClaude origin because:

- core CLI and TUI behavior were inherited from OpenClaude
- a large part of the current architecture still reflects OpenClaude naming and assumptions
- future maintainers need to know where original implementation patterns came from
- upstream diffs, debugging, and selective cherry-picks are much easier when lineage is documented

## Rebrand Policy

Going forward:

- user-facing references should prefer `ARC`
- system meaning should be `Agent Robotic Cluster`
- where historical context matters, say `ARC, derived from OpenClaude`
- internal leftovers named `openclaude` may still exist temporarily in code, tests, paths, or comments until they are migrated deliberately

## Current Practical State

The fork has already been separated in these key ways:

- CLI command is `arc`
- package name is `arc-openclaude`
- startup branding is `ARC`
- default isolated config directory is `~/.arc` when launched through `arc`

This keeps ARC from colliding with an existing `openclaude` install while preserving traceability back to the source project.
