# Repository Guidelines

## Project Structure & Module Organization

This Omarchy plugin displays authored open GitHub pull requests. Root-level QML files define the bar widget (`BarWidget.qml`), panel integration (`Panel.qml`), list content (`PrContent.qml`), and individual rows (`PrRow.qml`). `bin/github_pr_status.py` handles read-only GitHub requests and shared JSON caching. `manifest.json` declares the plugin; `install.py` copies runtime files into the user's plugin directory.

Python tests live in `tests/test_helper.py`; Qt Quick scenarios live in `tests/ui/`, with theme stubs in `tests/stubs/`. `preview.png` and `preview-light.png` use fictional data. Consult `README.md` for behavior and setup, and `VALIDATION.md` for recorded verification.

## Build, Test, and Development Commands

No compilation or package installation is required. Use Python 3.10+; the helper uses only the standard library.

- `python3 -m unittest discover -s tests -v`: run Python tests.
- `omarchy plugin validate .`: validate the plugin contract.
- `python3 tests/check_qml.py`: lint QML using installed Omarchy imports.
- `python3 tests/check_ui.py`: run offscreen Qt Quick tests and regenerate both previews.
- `python3 install.py --enable`: copy and enable the checkout inside an Omarchy session; rerun after edits because installation uses copies.
- `python3 bin/github_pr_status.py --force`: perform a live fetch using the active `gh` account.

QML checks require local Omarchy and Qt tools. Set `OMARCHY_PATH` for a nondefault Omarchy installation.

## Coding Style & Naming Conventions

Use four-space indentation. Follow existing Python `snake_case` functions and variables, `PascalCase` classes, and uppercase constants. QML components use `PascalCase` filenames and `camelCase` properties/signals. Use Omarchy theme tokens for fonts, spacing, and colors. Keep GitHub-provided text in `Text.PlainText`. QML lint is provided; no Python formatter is configured.

## Testing Guidelines

Use `unittest` with `test_*` methods and Qt Quick Test scenarios in `tst_*.qml`. Add regression coverage for changed behavior using fictional data and mocked API responses. Cover pagination, cache/account invalidation, and failure handling when modifying fetching. Check navigation and dark/light previews for UI changes. CI runs Python 3.10 and 3.14; QML checks remain local. No coverage threshold is configured.

## Commit & Pull Request Guidelines

Follow the short, imperative Git subjects already used, such as `Add Omarchy GitHub PR status plugin`. Describe the problem, resulting behavior, and checks run in PRs; link relevant issues and include screenshots for visual changes.

## Releasing Versions

When preparing a release:

1. Update `manifest.json` and add a dated, newest-first entry to `CHANGELOG.md`
   covering all user-visible changes and any new requirements or upgrade steps.
   Include both files in the release commit before creating its `vX.Y.Z` tag.
2. Run the Python suite, plugin validation, QML lint, and UI checks listed above.
   Inspect regenerated previews for UI changes and record checks and live-test
   limitations in `VALIDATION.md`.
3. Push the release commit and tag, verify GitHub Actions succeeds for that commit,
   then publish the GitHub release using the changelog entry as the basis for its
   notes. Verify the published release points to the intended tag. Keep published
   tags immutable; land later documentation corrections as follow-up commits.
4. When local deployment is in scope, update the installed copy separately and
   verify its version and runtime files. For UI changes, inspect the running panel;
   file copies and successful rescan/summon commands alone do not prove it loaded.
   If cached QML persists, use `omarchy restart shell` and inspect again.

## Security & Configuration

Preserve read-only GitHub access and cache permissions of 700/600 for directories/files. Live fetch output contains private PR metadata; use fictional examples in public reports and previews.
