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

`stage_cargo_source_notices.py` preserves the registry archives whose original
checksums occur in Deno's commit-bound `Cargo.lock`, together with their original
legal texts and declared metadata. An isolated `cargo vendor --locked` source
fetch does not compile or run crate build scripts. This inventory is explicitly
a workspace-source superset: build/test-only crates are not automatically
declared to be linked into the shipped executable. Missing legal text is listed
as a gap, never replaced with an invented upstream grant. Changed archives,
changed locks, other registries and unsafe notice members fail closed.

A nested dependency copyright file is not counted as the crate's missing root
license. Root and nested notice identifiers are recorded separately. When a
published crate omits root texts, `fetch_cargo_root_notices.py` collects original
texts only from the repository and full Git commit recorded inside that exact
checksum-verified crate. Every retained text has its exact public URL, hash and
size; missing provenance, 404s and download failures remain explicit gaps.
This collection does not decide whether a root grant covers every incorporated
dependency. Rusty V8's incorporated V8 commit and Ollama's MLX/MLX-C commits are
therefore independently pinned and retained as original source inputs.

App assembly checks the architecture of every actual Mach-O member before
signing any native member. A component specification's architecture label alone
does not establish that all its native dependencies can run on that CPU.

`stage_isolated_runtime.py` materializes an explicit clean speech prefix after
checking its Python input digest, architecture, wheel lock and any reviewed
Chatterbox source override. The original CPython legal text is
`python/lib/python3.12/LICENSE.txt`. Piper's wheel installs a separate GPL
`python/COPYING`; that file must not be mislabelled as Python's license. The
Piper and matching embedded eSpeak complete source archives are retained as
separate corresponding-source inputs. Python-package notice inventories and
final binary-source binding remain independent mandatory checks.

The measured ARM main and isolated speech prefixes contain 26,113 and 26,153
files respectively, before other components. Payload producer, component merger
and runtime reader therefore share a bounded 100,000-file / 32-MiB manifest
limit. The producer reserves one entry for its generated model catalog. These
larger bounded limits do not bypass per-file hash, architecture or path checks.

App source, launcher, icon, Info.plist and native input configuration are read
from bounded ordinary Git blobs at the recorded full source commit, rather than
copied from potentially changed working-tree files. Copied component bytes are
rechecked against their approved input hashes before shebang normalization or
signing may alter build output. Isolated speech provenance and source overrides
are revalidated after materialization as well as before it.

Native QtCore signing preflight demonstrated that signing its inner Mach-O
file alone leaves the framework bundle unsigned. Code-bearing nested framework,
app, bundle and plugin directories are therefore ad-hoc signed and strictly
verified bottom-up before final payload hashing. This structural build preflight
is not GUI, inference, installation or complete application acceptance evidence.

Public payload manifests and model catalogs use read-only-to-other-users 0644
permissions, not private-state 0600 defaults. This permits a different macOS
account to read an installed application without running it as administrator;
user preferences, jobs and acceptance data keep their private-state policy.

Piper's installed GPL COPYING must match the separately retained original
Piper source COPYING byte-for-byte. App assembly additionally requires the
matching Piper and embedded eSpeak archive digests from the committed public
source pins. A nested g2pW Apache notice in the wheel does not satisfy Piper's
primary GPL notice/source obligation.

`stage_media_executables.py` binds relocated FFmpeg, ffprobe and ImageMagick
executables to the matching original source notice identifiers. It validates
actual native architecture, executable hashes and retained source archive pins
before copying. FFmpeg/ffprobe explicitly reference the complete corresponding
source archive. Shared libraries remain in their independently attributed
native library stage; merging must preserve the graph's relative layout.
