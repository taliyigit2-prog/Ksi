# Apple Silicon release tooling

The current release is ARM64 only. Intel candidate workflows, relocation probes
and special Intel unit-proof reuse are retired. Common engine adapters and
historical acceptance APIs remain intact; they are not Intel release promises.
Tooling-only changes do not alter the accepted product. Every receipt remains
bound to the actual product commit, manifest and complete application tree.

Build-only model restoration retries transient provider outages at most four
times with bounded delays. It calls the unchanged product downloader with the
same official URL, size and SHA-256 pins each time. Changed hashes, unsafe URLs,
differing existing files and permanent HTTP errors fail without retry. Already
verified complete files remain usable; no account credentials are introduced.

Draft storage is opt-in and permitted only after genuine native packaged tests,
independent offline installation, complete application privacy scanning and
read-only media filename/xattr scanning. Potential dependency fixtures/compiler
paths are accepted only by exact original pinned public-byte comparisons.
Public-source archive pins were independently compared to their original
commit-bound archives; they are digest pins, not path exemptions.

`stage_reviewed_media.py` stages only Apple Silicon media in a source-bound
unpublished draft. Filenames, complete consecutive parts, integer sizes and
every required privacy check must match. Staging never publishes.

Only checksum-verified installation segments, transport, checksums and the
privacy receipt may be stored. The script never publishes, uploads the working
tree, or overwrites a differing asset. Public release and legal acceptance remain
separate gates. Original notices and corresponding sources remain in the app.

The final installer helper calls the existing strict `verify_distribution`
gate, requiring all 14 automatic cases, payload hashes, signing and read-only
DMG comparison. The historical two-architecture gate is not weakened. The helper
rejects Intel Macs, selects the physical Mac processor and downloads
only immutable checksum-pinned public release parts using stock macOS tools,
verifies every size/SHA-256, checks the internal-data filesystem and opens the
verified DMG for the standard Applications drag-and-drop installation. It never
overwrites differing existing files, installs Homebrew/Python, asks for an
account token, or silently bypasses Gatekeeper. A lost connection preserves
already verified complete segments. Its verification-only mode is tested on
owned synthetic files and is not substituted for real installation acceptance.
Distribute the executable helper in a ZIP preserving its executable mode.
