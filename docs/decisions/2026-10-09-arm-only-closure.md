# Apple Silicon-only completion and scoped cleanup

The owner retired Intel release work and approved removal of Intel-only work.
The supported release is macOS 14+ on Apple Silicon with 16 GB memory, offline
models on installation media, internal-disk storage and clearly labeled ad-hoc
signing. No Apple notarization or user acceptance testing is claimed.

Code cleanup follows the MIT-licensed
[Ponytail instructions](https://github.com/DietrichGebert/ponytail/blob/9cc65d03aa2da1db7121b912d03596409ee340b8/skills/ponytail/SKILL.md):
read callers, remove unused scope, reuse existing boundaries, retain validation
and error handling, and check affected behavior. No plugin, global hook or new
runtime dependency is installed. Ponytail applies to code cleanup only.

The Intel candidate/preflight workflows and Intel-specific relocation/unit-proof
helpers are removed. Remaining manual build workflows select ARM64 only. Common
engine adapters, locks, historical evidence and required original source/license
material are preserved. Deleted tracked files remain recoverable from Git.

The accepted product is not rebuilt for release-tool-only changes. The ARM
installer calls the existing strict per-distribution verifier, requiring every
one of its 14 acceptance cases and actual media comparison. Historical gates
are unchanged. Draft staging is ARM-only and cannot publish. The installer
rejects Intel, requires internal storage, validates every part and never silently
bypasses macOS security. Final publication remains blocked until privacy,
corresponding-source and native acceptance evidence are complete.
