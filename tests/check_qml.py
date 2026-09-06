#!/usr/bin/env python3
"""Lint against installed Omarchy modules without modifying the shell."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SHELL = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"


def imports(path):
    for module in ("Commons", "Ui", "services"):
        dest = path / "qs" / module
        dest.mkdir(parents=True)
        for source in (SHELL / module).iterdir():
            (dest / source.name).symlink_to(source)


if __name__ == "__main__":
    lint = shutil.which("qmllint") or "/usr/lib/qt6/bin/qmllint"
    with tempfile.TemporaryDirectory(prefix="pr-status-qml-") as temporary:
        imports(Path(temporary))
        # The host exposes QObject/var objects whose dynamic properties lint cannot infer.
        args = [lint, "-I", temporary, "--unqualified", "disable", "--missing-property", "disable"]
        args += [str(ROOT / name) for name in ("BarWidget.qml", "Panel.qml", "PrContent.qml", "PrRow.qml")]
        result = subprocess.run(args, capture_output=True, text=True)
        output = result.stdout + result.stderr
        print(output or "QML lint passed.")
        raise SystemExit(result.returncode or (1 if "Warning:" in output or "Error:" in output else 0))
