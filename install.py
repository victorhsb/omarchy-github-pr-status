#!/usr/bin/env python3
"""Install this checkout in the user-owned Omarchy plugin directory."""

import argparse
import json
import os
from pathlib import Path
import subprocess

PLUGIN_ID = "torugo.github-pr-status"
FILES = ("BarWidget.qml", "Panel.qml", "PrContent.qml", "PrRow.qml", "bin/github_pr_status.py", "README.md", "LICENSE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true", help="Enable the widget in the right bar section")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    subprocess.run(["omarchy", "plugin", "validate", str(source)], check=True)
    target = Path.home() / ".config/omarchy/plugins" / PLUGIN_ID
    if target.is_symlink():
        raise SystemExit("The destination must not be a symlink.")
    if target.exists():
        try:
            identity = json.loads((target / "manifest.json").read_text())["id"]
        except (OSError, ValueError, KeyError):
            raise SystemExit("Destination already exists without this plugin's manifest.")
        if identity != PLUGIN_ID:
            raise SystemExit("Destination belongs to another plugin.")
    names = FILES + tuple(name for name in ("preview.png", "preview-light.png") if (source / name).exists()) + ("manifest.json",)
    for name in names:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.is_symlink():
            raise SystemExit("Plugin subdirectories must not be symlinks.")
        data = (source / name).read_bytes()
        if destination.exists() and not destination.is_symlink() and destination.read_bytes() == data:
            continue
        temporary = destination.with_name(destination.name + ".new")
        with temporary.open("xb") as stream:
            stream.write(data)
        os.replace(temporary, destination)
    subprocess.run(["omarchy", "plugin", "validate", str(target)], check=True)
    subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], check=True)
    if args.enable:
        subprocess.run(["omarchy", "plugin", "enable", PLUGIN_ID, "--section", "right"], check=True)
    print(f"Installed {target}")


if __name__ == "__main__":
    main()
