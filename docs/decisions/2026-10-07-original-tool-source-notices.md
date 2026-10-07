# Original tool source and notice inventories

Ollama 0.24.0, Deno 2.9.6 and yt-dlp 2026.08.19 are bound to exact
official Git commits in `config/native-sources.json`. Their original source
archive digests and explicit legal-text selection are recorded separately in
`config/tool-source-notices.json`. Test-fixture licenses and source files whose
names contain “license” are not interpreted as runtime attributions.

`stage_original_tool_sources.py` preserves the selected original texts and full
source archives as explicit component stages. The retained review inventory
states remaining dependency-closure work. Collecting these files is not itself
a redistribution approval, functional test, or release gate.

`fetch_go_binary_notices.py` inspects a native executable with `go version -m`;
it does not execute the tool. Its architecture and source revision must match,
and each dependency's exact version and h1 checksum must occur in the original
commit-bound `go.sum`. Explicitly authorized downloads use the public Go proxy
and checksum database in a dedicated build cache, with automatic toolchain
selection and private module settings disabled. Original module archives and
notice hashes are retained; no dependency code is compiled or run.

Universal Mach-O binaries must be inspected one architecture slice at a time.
Inspecting a universal executable's default Go build information can describe
its first slice rather than the requested host architecture. Intel inspection
on an ARM machine does not establish native Intel functional acceptance.
