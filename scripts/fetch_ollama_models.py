#!/usr/bin/env python3
"""Fetch a complete pinned official Ollama model without a running server."""

import argparse
import json
import re
from pathlib import Path

from ksi_local.build_inputs import fetch_pinned_input


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name", choices=["translategemma", "qwen3.5"])
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    catalog = json.loads((Path(__file__).resolve().parents[1] / "config/ollama-model-sources.json").read_text())
    model = next(row for row in catalog["models"] if row["name"] == args.name)
    root = args.destination.absolute()
    manifest = fetch_pinned_input(model["manifest"], root / "manifests/registry.ollama.ai/library" / model["name"] / model["tag"])
    data = json.loads(manifest.read_text())
    actual = [data["config"], *data["layers"]]
    expected = {(row["sha256"], row["size"]) for row in model["layers"]}
    if len(actual) != len(expected) or {(row["digest"].removeprefix("sha256:"), row["size"]) for row in actual} != expected:
        raise ValueError("Ollama manifest layers differ from the reviewed complete package.")
    for index, row in enumerate(model["layers"]):
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            raise ValueError("Ollama layer digest is invalid.")
        entry = dict(row, url="https://registry.ollama.ai/v2/library/" + model["name"] + "/blobs/sha256:" + row["sha256"])
        fetch_pinned_input(entry, root / "blobs" / ("sha256-" + row["sha256"]))
        print(f"Verified {model['name']} layers: {index + 1}/{len(model['layers'])}", flush=True)


if __name__ == "__main__":
    main()
