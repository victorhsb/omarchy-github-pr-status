#!/usr/bin/env python3
"""Read-only GitHub PR monitor. stdout is one versioned JSON snapshot."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time


PAGE = "pageInfo { hasNextPage endCursor }"
BASIC = """id number title url state isDraft updatedAt
author { login } repository { nameWithOwner }"""
CHECKS = """nodes { __typename
 ... on CheckRun { id name status conclusion detailsUrl }
 ... on StatusContext { id context state targetUrl }
} """ + PAGE
REVIEWS = "nodes { id state comments { totalCount } } " + PAGE
DETAIL = BASIC + """
reviewDecision comments { totalCount }
reviews(first:100) { """ + REVIEWS + """ }
commits(last:1) { nodes { commit { oid statusCheckRollup {
 contexts(first:100) { """ + CHECKS + """ }
} } } }
"""
SEARCH = """query($q:String!, $cursor:String) {
 search(query:$q, type:ISSUE, first:100, after:$cursor) {
 issueCount nodes { ... on PullRequest { """ + BASIC + " } } " + PAGE + " } }"
DETAILS = "query($ids:[ID!]!) { nodes(ids:$ids) { ... on PullRequest { " + DETAIL + " } } }"


class FetchError(Exception):
    def __init__(self, message, kind="api", retry_after=0):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


class GitHub:
    def graphql(self, query, **variables):
        env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_HOST="github.com")
        try:
            proc = subprocess.run(
                ["gh", "api", "--hostname", "github.com", "graphql", "--input", "-"],
                input=json.dumps({"query": query, "variables": variables}),
                text=True, capture_output=True, timeout=35, env=env,
            )
        except FileNotFoundError as exc:
            raise FetchError("Install GitHub CLI (gh), then run gh auth login.", "auth") from exc
        except subprocess.TimeoutExpired as exc:
            raise FetchError("GitHub request timed out. Retrying automatically.", "network") from exc
        try:
            payload = json.loads(proc.stdout) if proc.stdout else {}
        except ValueError as exc:
            raise FetchError("GitHub returned an invalid response.") from exc
        if proc.returncode or payload.get("errors"):
            # Only classify stderr: do not copy arbitrary CLI output or credentials to the UI/cache.
            message = (proc.stderr + json.dumps(payload.get("errors", []))).lower()
            if any(word in message for word in ("rate limit", "rate_limit", "rate_limited", "abuse")):
                raise FetchError("GitHub rate limit reached. Retrying in 15 minutes.", "rate_limit", 900)
            if any(word in message for word in ("auth login", "bad credentials", "401", "authentication", "token has expired")):
                raise FetchError("Sign in with gh auth login --hostname github.com.", "auth")
            if any(word in message for word in ("resolve", "connection", "network", "timeout")):
                raise FetchError("Cannot reach GitHub. Check your connection.", "network")
            raise FetchError("GitHub could not return PR details. Check account access and retry.")
        if not isinstance(payload.get("data"), dict):
            raise FetchError("GitHub returned no data.")
        return payload["data"]


def next_cursor(connection, previous=None):
    info = connection["pageInfo"]
    if not info["hasNextPage"]:
        return None
    cursor = info["endCursor"]
    if not cursor or cursor == previous:
        raise FetchError("GitHub pagination did not advance; results are incomplete.")
    return cursor


def discover(api, login):
    cursor, found, total, pages = None, {}, 0, 0
    while True:
        page = api.graphql(SEARCH, q=f"is:pr is:open author:{login} sort:updated-desc", cursor=cursor)["search"]
        total = max(total, page["issueCount"])
        pages += 1
        for pr in page["nodes"]:
            if pr and pr.get("state") == "OPEN" and (pr.get("author") or {}).get("login", "").lower() == login.lower():
                found[pr["id"]] = pr
        cursor = next_cursor(page, cursor)
        if cursor is None or pages >= 10:
            break
    incomplete = total > 1000 or cursor is not None
    return sorted(found.values(), key=lambda pr: pr["updatedAt"], reverse=True), incomplete


def bucket(check):
    status = check.get("status") if check.get("__typename") == "CheckRun" else check.get("state")
    if status in {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}:
        return "running"
    result = check.get("conclusion") if status == "COMPLETED" else status
    if result == "SUCCESS":
        return "success"
    if result in {"SKIPPED", "NEUTRAL", "CANCELLED"}:
        return "skipped"
    if result in {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}:
        return "failed"
    return "unknown"


def basic_row(pr):
    return {
        "id": pr["id"], "number": pr["number"], "title": pr["title"], "url": pr["url"],
        "repository": pr["repository"]["nameWithOwner"], "author": pr["author"]["login"],
        "updatedAt": pr["updatedAt"], "draft": pr["isDraft"], "review": "Unknown",
        "discussion": None, "inline": None, "checks": None, "counts": None,
        "error": "Details unavailable", "fetchedAt": None,
    }


def review_pages(api, pr):
    connection, seen, count, cursor = pr["reviews"], set(), 0, None
    while True:
        for review in connection["nodes"]:
            if review["id"] not in seen and review["state"] != "PENDING":
                # Every inline reply belongs to a review; totalCount avoids downloading bodies.
                count += review["comments"]["totalCount"]
                seen.add(review["id"])
        cursor = next_cursor(connection, cursor)
        if cursor is None:
            return count
        data = api.graphql("query($id:ID!, $cursor:String!) { node(id:$id) { ... on PullRequest { reviews(first:100, after:$cursor) { " + REVIEWS + " } } } }", id=pr["id"], cursor=cursor)
        connection = data["node"]["reviews"]


def check_pages(api, pr):
    commits = pr["commits"]["nodes"]
    if not commits:
        raise FetchError("PR head commit is unavailable.")
    commit = commits[-1]["commit"]
    rollup = commit["statusCheckRollup"]
    if rollup is None:
        return []
    connection, found, cursor = rollup["contexts"], {}, None
    while True:
        for check in connection["nodes"]:
            if check:
                found[check["id"]] = {
                    "name": check.get("name", check.get("context", "Check")),
                    "status": check.get("conclusion") or check.get("status") or check.get("state") or "UNKNOWN",
                    "bucket": bucket(check),
                }
        cursor = next_cursor(connection, cursor)
        if cursor is None:
            return list(found.values())
        # Pin subsequent pages to the original commit, even if a push races this fetch.
        owner, name = pr["repository"]["nameWithOwner"].split("/", 1)
        data = api.graphql("query($owner:String!, $name:String!, $oid:GitObjectID!, $cursor:String!) { repository(owner:$owner,name:$name) { object(oid:$oid) { ... on Commit { statusCheckRollup { contexts(first:100,after:$cursor) { " + CHECKS + " } } } } } }", owner=owner, name=name, oid=commit["oid"], cursor=cursor)
        connection = data["repository"]["object"]["statusCheckRollup"]["contexts"]


def normalize(api, pr):
    row = basic_row(pr)
    checks = check_pages(api, pr)
    counts = {key: 0 for key in ("running", "success", "skipped", "failed", "unknown")}
    for check in checks:
        counts[check["bucket"]] += 1
    row.update(
        review={"APPROVED": "Approved", "CHANGES_REQUESTED": "Changes requested", "REVIEW_REQUIRED": "Review required", None: "No review decision"}.get(pr["reviewDecision"], "Unknown"),
        discussion=pr["comments"]["totalCount"], inline=review_pages(api, pr),
        checks=checks, counts=counts, error=None, fetchedAt=time.time(),
    )
    return row


def snapshot(api, login, previous):
    discovered, truncated = discover(api, login)
    rows, errors = [], []
    old = {pr["id"]: pr for pr in previous.get("prs", [])}
    retry = 0
    for start in range(0, len(discovered), 20):
        batch = discovered[start:start + 20]
        batch_error = None
        try:
            nodes = api.graphql(DETAILS, ids=[pr["id"] for pr in batch])["nodes"]
            details = {pr["id"]: pr for pr in nodes if pr}
        except FetchError as exc:
            details, batch_error = {}, exc
        for source in batch:
            try:
                if retry:
                    raise FetchError("Refresh paused: GitHub rate limit reached.", "rate_limit", retry)
                if batch_error:
                    raise batch_error
                pr = details.get(source["id"])
                if not pr:
                    raise FetchError("PR details are unavailable.")
                if pr["state"] != "OPEN" or (pr.get("author") or {}).get("login", "").lower() != login.lower():
                    continue
                rows.append(normalize(api, pr))
            except (FetchError, KeyError, TypeError) as exc:
                if isinstance(exc, FetchError) and exc.kind == "auth":
                    raise exc
                row = copy.deepcopy(old.get(source["id"], basic_row(source)))
                row.update({key: value for key, value in basic_row(source).items() if key in {"title", "draft", "updatedAt", "url"}})
                row["error"] = str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response."
                rows.append(row)
                errors.append(row["error"])
                retry = max(retry, getattr(exc, "retry_after", 0))
        if retry:
            # Stop requesting after a rate limit; retain explicit unknown/stale rows for the remainder.
            for source in discovered[start + 20:]:
                row = copy.deepcopy(old.get(source["id"], basic_row(source)))
                row["error"] = "Refresh paused: GitHub rate limit reached."
                rows.append(row)
            break
    now = time.time()
    error = "Some PR details are unavailable. " + errors[0] if errors else ""
    if truncated:
        error = "GitHub search is limited to 1,000 results; this list is incomplete. " + error
    return {
        "schemaVersion": 1, "account": login, "prs": sorted(rows, key=lambda row: row["updatedAt"], reverse=True),
        "partial": bool(errors or truncated), "stale": bool(errors or truncated), "error": error,
        "fetchedAt": now, "lastSuccessAt": previous.get("lastSuccessAt") if errors or truncated else now,
        "retryAt": now + retry if retry else 0,
    }


class Cache:
    def __init__(self, path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise FetchError("Cache directory must be owned by you and must not be a symlink.", "cache")
        os.chmod(path, 0o700)
        self.path = path
        self.fd = None

    def lock(self):
        self.fd = os.open(self.path / "refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)

    def read(self):
        try:
            fd = os.open(self.path / "snapshot.json", os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                value = json.load(stream)
            return value if value.get("schemaVersion") == 1 else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def write(self, data):
        name = self.path / f"snapshot.{os.getpid()}.tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
            os.replace(name, self.path / "snapshot.json")
        finally:
            name.unlink(missing_ok=True)


def auth_fingerprint():
    """Invalidate cached private data on gh account/config/token changes, without storing tokens."""
    home = Path(os.environ.get("GH_CONFIG_DIR", str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gh")))
    digest = hashlib.sha256()
    for filename in ("hosts.yml", "config.yml"):
        try:
            digest.update((home / filename).read_bytes())
        except OSError:
            pass
    digest.update(os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", "")).encode())
    return digest.hexdigest()


def run(api, cache, interval=60, force=False, now=time.time):
    cache.lock()
    try:
        previous = cache.read()
        fingerprint = auth_fingerprint()
        if previous.get("authFingerprint") != fingerprint:
            previous = {}
        timestamp = now()
        age = timestamp - previous.get("attemptedAt", 0)
        # Coalesce simultaneous manual requests and respect server backoff even on manual refresh.
        if previous and (previous.get("retryAt", 0) > timestamp or 0 <= age < (3 if force else interval)):
            return previous
        try:
            login = api.graphql("query { viewer { login } }")["viewer"]["login"]
            if previous.get("account") != login:
                previous = {}
            result = snapshot(api, login, previous)
        except (FetchError, KeyError, TypeError) as exc:
            auth = isinstance(exc, FetchError) and exc.kind == "auth"
            result = {} if auth else copy.deepcopy(previous)
            result.update(schemaVersion=1, stale=True, partial=result.get("partial", False),
                          error=str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response.")
            result.setdefault("prs", [])
            result["failures"] = previous.get("failures", 0) + 1
            result["retryAt"] = timestamp + max(getattr(exc, "retry_after", 0), min(900, 60 * 2 ** min(result["failures"] - 1, 4)))
        result.update(attemptedAt=timestamp, authFingerprint=fingerprint)
        cache.write(result)
        return result
    finally:
        cache.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, choices=(20, 60), default=60)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        path = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "omarchy-github-pr-status"
        result = run(GitHub(), Cache(path), args.interval, args.force)
    except (OSError, FetchError) as exc:
        result = {"schemaVersion": 1, "prs": [], "stale": True, "error": "Cannot use PR cache: " + str(exc)}
    # The fingerprint is only for internal cache invalidation.
    print(json.dumps({key: value for key, value in result.items() if key != "authFingerprint"}))


if __name__ == "__main__":
    main()
