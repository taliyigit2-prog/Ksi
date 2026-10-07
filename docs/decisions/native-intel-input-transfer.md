# Reviewed native Intel candidate inputs

Native Intel validation imports a separately reviewed, SHA-256-bound archive into
a fresh internal-disk build directory. The importer checks every transport part,
the joined digest, bounded archive paths and anonymous ownership metadata, then
verifies component sizes/digests and architecture/runtime provenance. It rejects
links, special files, duplicate paths, malformed inventory and existing targets.
Original model weights and notices are not rewritten. No user jobs, credentials
or local settings belong in these inputs.

The manually dispatched Intel candidate workflow downloads these inputs from an
unpublished draft release using a short-lived repository token. Draft visibility
requires push access, so this job has contents-write permission; the token is
provided only to the download step and checkout does not persist credentials.
The workflow publishes only small build/test evidence, not the application or
the input archive. Draft inputs are not a public product release.

Archive import, native assembly and source tests are distinct from product
acceptance. They do not attest model quality, complete installation, privacy or
license closure. Final release still requires the independently bound automatic
acceptance gates for both native architectures.
