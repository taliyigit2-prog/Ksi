"""Real KSI Local Studio front-end entry used by the official PySide/Nuitka pilot."""

from __future__ import annotations

import json
import platform
import sys

import PySide6
import docx
import lingua
import pypdf

from ksi_local import __version__


def self_test() -> int:
    payload = {
        "application": "KSI Local Studio",
        "version": __version__,
        "architecture": platform.machine(),
        "compiled": "__compiled__" in globals(),
        "python": platform.python_version(),
        "embedded_packages": {
            "PySide6": PySide6.__version__,
            "python-docx": docx.__version__,
            "pypdf": pypdf.__version__,
            "lingua": bool(lingua.LanguageDetectorBuilder),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["compiled"] and platform.machine() == "arm64" else 1


def main() -> int:
    if "--phase19-self-test" in sys.argv:
        return self_test()
    from ksi_local.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
