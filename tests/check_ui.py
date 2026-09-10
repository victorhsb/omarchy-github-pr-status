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
    (imports / "qs/Ui/qmldir").write_text("module qs.Ui\nPanelKeyCatcher 1.0 PanelKeyCatcher.qml\nPanel 1.0 Panel.qml\nKeyboardPanel 1.0 KeyboardPanel.qml\n")
    (imports / "qs/Ui/Panel.qml").write_text("""import QtQuick
Item {
    property string moduleName
    property bool manageIpc
    property var bar: null
    property bool opened: true
}
""")
    (imports / "qs/Ui/KeyboardPanel.qml").write_text("""import QtQuick
Item {
    property var anchorItem
    property var owner
    property var bar
    property bool open
    property var focusTarget
    property real contentWidth
    property real contentHeight
    function fittedContentWidth(value) { return value }
    function fittedContentHeight(value) { return value }
    width: contentWidth
    height: contentHeight
}
""")
    # Exercise the real QML Process without touching the desktop clipboard.
    copier = imports / "wl-copy"
    copier.write_text("""#!/usr/bin/python3
import sys
expected = ["--type", "text/plain", "--"]
valid = ["https://github.com/example/tools/pull/42", "example/tools#42"]
sys.exit(0 if sys.argv[1:4] == expected and sys.argv[4:] in [[v] for v in valid] else 1)
""")
    copier.chmod(0o700)
    env["PATH"] = str(imports) + os.pathsep + env.get("PATH", "")
    subprocess.run([runner, "-input", str(root / "ui"), "-import", str(imports)], env=env, check=True)
    env["QML_IMPORT_PATH"] = str(imports)
    runtime = imports / "runtime"
    runtime.mkdir(mode=0o700)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env.pop("WAYLAND_DISPLAY", None)
    plugin = imports / "plugin"
    plugin.mkdir()
    for name in ("Panel.qml", "PrContent.qml", "PrRow.qml"):
        shutil.copyfile(root.parent / name, plugin / name)
    harness = imports / "shell.qml"
    harness.write_text((root / "clipboard.qml").read_text().replace('import ".." as Plugin', 'import "plugin" as Plugin'))
    # Quickshell modules are static plugins unavailable to qmltestrunner.
    # Run the production Panel with the real Process in an isolated shell.
    quickshell = shutil.which("quickshell")
    for missing in (False, True):
        if missing:
            copier.unlink()
            env["PATH"] = str(imports)
            env["TEST_COPY_MISSING"] = "1"
        subprocess.run([quickshell, "-p", str(harness)], env=env, check=True, timeout=15)
