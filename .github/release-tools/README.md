# Native release tooling

The Intel workflow checks out an explicit full product commit and this tooling
separately. Tooling-only improvements therefore do not relabel or alter an
already accepted product. Each native receipt and draft remains bound to the
actual product source, manifest and complete application tree.

The auxiliary checkout lives under generated `build/`, outside the canonical
source inventory. An early native relocation probe moves only the newly built
app into an owned fixture directory, checks first-offline GUI and real speech,
then restores its original path even on failure. Diagnostics are redacted before
upload. Independent installation from the final DMG is still tested separately.

Optional source-unit proof reuse is restricted to one independently audited
675-test native Intel success with identical immutable product Git tree and
actual SDK manifest. Affected repository identity tests run again. This never
reuses model or installation acceptance and explicitly records that the original
overall workflow failed later during installation.

Draft storage is opt-in and permitted only after genuine native packaged tests,
independent offline installation, complete application privacy scanning and
read-only media filename/xattr scanning. Potential dependency fixtures/compiler
paths are accepted only by exact original pinned public-byte comparisons.
Public-source archive pins were independently compared to their original
commit-bound archives; they are digest pins, not path exemptions.

Only checksum-verified installation segments, transport, checksums and the
privacy receipt may be stored. The script never publishes, uploads the working
tree, or overwrites a differing asset. Public release and legal acceptance remain
separate gates. Original notices and corresponding sources remain in the app.

The final installer helper is generated only by calling the strict two-native-
architecture release verifier. It selects the physical Mac processor, downloads
only immutable checksum-pinned public release parts using stock macOS tools,
verifies every size/SHA-256, checks the internal-data filesystem and opens the
verified DMG for the standard Applications drag-and-drop installation. It never
overwrites differing existing files, installs Homebrew/Python, asks for an
account token, or silently bypasses Gatekeeper. A lost connection preserves
already verified complete segments. Its verification-only mode is tested on
owned synthetic files and is not substituted for real installation acceptance.
