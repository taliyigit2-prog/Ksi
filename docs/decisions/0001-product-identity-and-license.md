# ADR 0001: Product identity and source license

- Status: accepted
- Date: 2026-09-21

## Decision

The public product is named **KSI Local Studio**. Its source distribution is
`ksi-local-studio`, its Python package is `ksi_local`, and its command is `ksi`.
KSI-owned source code is licensed under Apache License 2.0.

Third-party executables, libraries, models and assets keep their own licenses.
GPL applications are integrated only as clearly separate optional processes;
their source is not copied into KSI. The exact release composition must pass a
license and SBOM gate before publication.

## Consequences

- The predecessor remains private and is migrated one way.
- New code must use canonical identifiers from `project_metadata.py`.
- A clean public Git history will be created only after privacy cleanup.
- Binary releases need their own dependency and license inventory.

