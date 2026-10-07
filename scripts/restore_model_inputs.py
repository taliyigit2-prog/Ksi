#!/usr/bin/env python3
"""Restore exact original public model inputs on a build machine, not first app launch."""
import argparse
import json
from pathlib import Path
from ksi_local.model_input_restore import restore_model_inputs

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("imported_inputs", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    model_sources = json.loads((repository / "config/model-sources.json").read_bytes())
    ollama_sources = json.loads((repository / "config/ollama-model-sources.json").read_bytes())
    print(json.dumps(restore_model_inputs(args.imported_inputs.absolute(), model_sources,
                                         ollama_sources, allow_network=args.allow_network)))
