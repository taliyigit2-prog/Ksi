# Contributing to KSI Local Studio

Thank you for helping improve KSI Local Studio.

1. Read `AGENTS.md`, `docs/AI_CONTRIBUTOR_GUIDE.md` and `docs/ARCHITECTURE.md`.
2. Keep the canonical product, package, commands, workspace and `KSI_` environment prefix.
3. Do not add user media, jobs, databases, URLs, cookies, browser profiles, models, local
   UUIDs, credentials, generated apps or acceptance artifacts.
4. Keep local processing as the default. New network behavior must be explicit and tested.
5. Preserve sources and completed outputs; destructive work needs preview and confirmation.
6. Add automated tests and update public documentation for every behavior change.
7. Update third-party notices and the SBOM when adding a dependency.

Run before opening a pull request:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python scripts/build_public_source.py /new/output/directory
```

By submitting a contribution, you agree that it is licensed under Apache-2.0 unless a
file clearly states a compatible third-party license.

