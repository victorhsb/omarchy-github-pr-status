# Changelog

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
