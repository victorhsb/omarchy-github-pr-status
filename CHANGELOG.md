# Changelog

## 1.1.2 — 2026-09-09

- Fix missing-icon placeholders in ready-to-merge notifications by bundling a
  green pull-request SVG and referencing its absolute path.
- Include the icon in local installations and add regression coverage for
  notification delivery from copied installations, including paths with spaces.

No new requirements or special upgrade steps. The bundled icon is used by new
notifications after updating.

## 1.1.1 — 2026-09-09

- Replace the Open pill with a solid green “✓ Ready to merge” badge when fresh
  data confirms readiness, retaining the subtle card tint and border.
- Allow cancelled checks alongside passing CI when GitHub reports MERGEABLE and
  CLEAN, matching its ready-to-merge state. Drafts, stale/partial data,
  review/merge blockers, and running, failed, or unknown checks still suppress
  the badge.
- Add badge regression coverage and refresh the fictional dark/light previews.

No new requirements. If the old badge persists after updating, run
`omarchy restart shell` to reload cached QML components; this briefly reloads
the bar and desktop overlays.

## 1.1.0 — 2026-09-09

- Notify when a PR previously needing review becomes approved and ready to merge.
  Pending review cycles survive restarts and wait for CI; shared cache state
  prevents duplicate alerts across displays. Requires `notify-send` (libnotify).
- Group stacked PRs under separate headings with indented cards and a connecting
  guide. Order members bottom-to-top using GitHub's actual position/size labels,
  and order groups by their latest visible PR update. Keep the authored/open-only
  scope, including drafts; no `gh stack` extension or local checkout is needed.
- Give merge-ready PRs with passing CI a subtle green tint and border. Stale data,
  review/merge blockers, and running, failed, unknown, or cancelled checks suppress
  the accent.
- Expand regression coverage and refresh the fictional dark/light previews.

If the old layout persists after updating, run `omarchy restart shell` to reload
cached QML components; this briefly reloads the bar and desktop overlays.

## 1.0.1 — 2026-09-07

Security fix for externally influenceable pagination and memory growth during
GitHub PR refreshes.

- Enforce a shared 60-second refresh deadline, including cache lock waiting and
  all API requests.
- Cap discovery at 100 PRs and ten search pages; cap checks and reviews at five
  pages and 500 entries each per PR.
- Bound GitHub CLI output and retained strings; terminate timed-out or oversized
  requests. Validate cached data before reuse and cap serialized snapshots at
  2 MiB.
- Mark capped results incomplete/stale, retain safe previous details or show
  unknown values, and preserve the last complete update timestamp. Omit oldest
  PRs when necessary to fit the snapshot budget.
- Add regression coverage for deadlines, subprocess limits, cache validation,
  and bounded JSON output.

## 1.0.0 — 2026-09-06

First stable release of GitHub PR Status for Omarchy.

- Monitor your authored open pull requests, including drafts, across repositories.
- Show check status bars, approval state, and separate discussion/inline counts.
- Browse individual checks and open PRs using the mouse or keyboard.
- Reuse GitHub CLI authentication, including access to private repositories.
- Share refreshes between displays, paginate checks and reviews, and retain
  explicitly stale results when requests fail.
- Include dark/light previews, regression tests, and Git-based installation docs.

Requires Omarchy's Quattro plugin runtime, Python 3.10+, and GitHub CLI.
Tested desktop: Omarchy 4.0.2 with Qt 6.11.2. Supports the active github.com account;
Enterprise hosts, multiple simultaneous accounts, and GitHub write actions are
outside this release.
