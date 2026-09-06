#!/usr/bin/env python3
"""Exercise production content QML with isolated theme tokens and fictional PRs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parent
runner = shutil.which("qmltestrunner") or "/usr/lib/qt6/bin/qmltestrunner"
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QPA_PLATFORMTHEME="generic")
with tempfile.TemporaryDirectory(prefix="pr-ui-") as temporary:
    imports = Path(temporary)
    (imports / "qs/Ui").mkdir(parents=True)
    (imports / "qs/Commons").symlink_to(root / "stubs/qs/Commons", target_is_directory=True)
    shell = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
    (imports / "qs/Ui/PanelKeyCatcher.qml").symlink_to(shell / "Ui/PanelKeyCatcher.qml")
    (imports / "qs/Ui/qmldir").write_text("module qs.Ui\nPanelKeyCatcher 1.0 PanelKeyCatcher.qml\n")
    raise SystemExit(subprocess.call([runner, "-input", str(root / "ui"), "-import", str(imports)], env=env))
