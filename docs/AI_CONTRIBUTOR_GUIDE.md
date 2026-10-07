# KSI Local Studio AI contributor guide

## Canonical identity

| Concept | Canonical value |
|---|---|
| Product | KSI Local Studio |
| Short name | KSI |
| Repository | `ksi-local-studio` |
| Python distribution | `ksi-local-studio` |
| Python package | `ksi_local` |
| Command | `ksi` |
| GUI command | `ksi-gui` |
| MCP server | `ksi-local-studio` |
| Environment prefix | `KSI_` |
| Internal workspace | `KSI-Workspace` |

Never invent an alternative spelling. A private predecessor exists only for a
one-way local data migration. It is not a current product name or import path.

## Architectural rules

1. GUI, CLI and MCP must call the same `ksi_local` service layer.
2. GUI automation must not be used as an application API.
3. Models and large media stay outside the Git repository.
4. Only one memory-heavy local model may be loaded at a time on the supported
   16 GB Apple Silicon baseline.
5. Network access must be explicit. Local processing is the default.
6. Cookies, account sessions, URLs containing credentials and user documents
   must never be written to logs or committed.
7. Active workspaces, models and job caches live on the system's internal data
   disk. Legacy external selections are preserved privately for migration only.
8. Destructive cleanup requires a preview and explicit user confirmation.
9. Third-party code, models and assets retain their own licenses.
10. New behavior requires automated tests and an update to the relevant public
    documentation.

## Repository hygiene

- Do not commit virtual environments, model weights, generated applications,
  databases, cookies, logs, user jobs, acceptance artifacts or local UUIDs.
- Put generated public documentation assets under `docs/assets/` only after
  privacy review.
- Record architectural decisions under `docs/decisions/`.
- Update `THIRD_PARTY_NOTICES.md` and the SBOM when adding a dependency.

## Validation

Run the unit suite with the project runtime and `PYTHONPATH=src`. Packaging and
model tests are separate acceptance gates and must not silently download data.
