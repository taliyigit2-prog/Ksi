# Migrating the private predecessor

The private predecessor is not a public product or supported import namespace.
KSI provides a one-way, local migration that:

1. inspects the old and canonical locations without writing;
2. refuses to continue when both locations exist;
3. moves same-volume directories atomically;
4. archives the original jobs database and verifies its SHA-256 digest;
5. rewrites stored job-directory prefixes to `KSI-Workspace`;
6. writes a local migration report outside the public repository.

Dry-run:

```bash
ksi migrate-predecessor --mount /Volumes/EXTERNAL_DISK
```

Apply only after the KSI application bundle is ready:

```bash
ksi migrate-predecessor --mount /Volumes/EXTERNAL_DISK --apply
```

