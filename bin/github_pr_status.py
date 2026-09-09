#!/usr/bin/env python3
"""Read-only GitHub PR monitor. stdout is one versioned JSON snapshot."""

import argparse
import copy
import fcntl
import hashlib
import html
import json
import math
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time


REFRESH_SECONDS = 60
REQUEST_SECONDS = 35
MAX_PRS = 100
MAX_SEARCH_PAGES = 10
MAX_DETAIL_PAGES = 5
MAX_ENTRIES = 500
MAX_STDOUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_TEXT = 512
MAX_STATUS = 64
MAX_NAME = 256
MAX_REFERENCE = 1024
BUCKETS = ("running", "success", "skipped", "failed", "unknown")

PAGE = "pageInfo { hasNextPage endCursor }"
BASIC = """id number title url state isDraft updatedAt
author { login } repository { nameWithOwner }"""
CHECKS = """nodes { __typename
 ... on CheckRun { id name status conclusion }
 ... on StatusContext { id context state }
} """ + PAGE
REVIEWS = "nodes { id state comments { totalCount } } " + PAGE
DETAIL = BASIC + """
reviewDecision mergeable mergeStateStatus comments { totalCount }
stackEntry { position stack { number size } }
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


class LimitError(FetchError):
    def __init__(self, message):
        super().__init__(message, "limit")


class Deadline:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.end = clock() + REFRESH_SECONDS

    def remaining(self):
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise FetchError("Refresh time limit reached; results are incomplete.", "deadline")
        return remaining


class BudgetAPI:
    def __init__(self, api, deadline):
        self.api, self.deadline = api, deadline

    def graphql(self, query, **variables):
        try:
            result = self.api.graphql(query, timeout=min(REQUEST_SECONDS, self.deadline.remaining()), **variables)
        except FetchError as exc:
            if exc.kind not in {"auth", "rate_limit"}:
                self.deadline.remaining()
            raise
        self.deadline.remaining()
        return result


def budget_api(api):
    return api if isinstance(api, BudgetAPI) else BudgetAPI(api, Deadline())


def bounded_process(command, request, timeout, env):
    """Drain both pipes without ever buffering unlimited CLI output."""
    end = time.monotonic() + timeout
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env) as proc:
        try:
            # Include stdin in the deadline: even a child that never reads must not block us.
            pending = memoryview(request.encode("utf-8"))
            output = {"stdout": bytearray(), "stderr": bytearray()}
            with selectors.DefaultSelector() as selector:
                for stream, name in ((proc.stdin, "stdin"), (proc.stdout, "stdout"), (proc.stderr, "stderr")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ, name)
                while selector.get_map():
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(remaining):
                        if key.data == "stdin":
                            try:
                                sent = os.write(key.fd, pending[:4096])
                                pending = pending[sent:]
                            except BrokenPipeError:
                                pending = pending[:0]
                            if not pending:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        limit = MAX_STDOUT_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                        buffer = output[key.data]
                        chunk = os.read(key.fd, min(65536, limit + 1 - len(buffer)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer.extend(chunk)
                        if len(buffer) > limit:
                            raise LimitError("GitHub response size limit reached; results are incomplete.")
            proc.wait(timeout=max(0, end - time.monotonic()))
            return subprocess.CompletedProcess(command, proc.returncode,
                                               output["stdout"].decode("utf-8", errors="replace"),
                                               output["stderr"].decode("utf-8", errors="replace"))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


class GitHub:
    def graphql(self, query, timeout=REQUEST_SECONDS, **variables):
        env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_HOST="github.com")
        try:
            proc = bounded_process(
                ["gh", "api", "--hostname", "github.com", "graphql", "--input", "-"],
                json.dumps({"query": query, "variables": variables}), timeout, env,
            )
        except FileNotFoundError as exc:
            raise FetchError("Install GitHub CLI (gh), then run gh auth login.", "auth") from exc
        except subprocess.TimeoutExpired as exc:
            raise FetchError("GitHub request timed out. Retrying automatically.", "network") from exc
        try:
            payload = json.loads(proc.stdout) if proc.stdout else {}
        except (ValueError, RecursionError) as exc:
            raise FetchError("GitHub returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise FetchError("GitHub returned an invalid response.")
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
    cursor = reference(info["endCursor"])
    if not cursor or cursor == previous:
        raise FetchError("GitHub pagination did not advance; results are incomplete.")
    return cursor


def reference(value, limit=MAX_REFERENCE):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise LimitError("GitHub identifier or URL exceeds the supported limit.")
    return value


def display_text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        raise FetchError("Incomplete GitHub response.")
    return value if len(value) <= limit else value[:limit - 1] + "…"


def source_record(pr):
    """Keep only bounded fields needed for discovery and stale fallbacks."""
    return {
        "id": reference(pr["id"]), "number": integer(pr["number"]),
        "title": display_text(pr["title"]), "url": reference(pr["url"]),
        "repository": {"nameWithOwner": reference(pr["repository"]["nameWithOwner"], MAX_NAME)},
        "author": {"login": reference(pr["author"]["login"], MAX_NAME)},
        "updatedAt": reference(pr["updatedAt"], MAX_STATUS),
        "state": reference(pr["state"], MAX_STATUS), "isDraft": boolean(pr["isDraft"]),
    }


def discover(api, login):
    api = budget_api(api)
    reference(login, MAX_NAME)
    cursor, found, total, pages, reason = None, {}, 0, 0, ""
    while True:
        try:
            page = api.graphql(SEARCH, q=f"is:pr is:open author:{login} sort:updated-desc", cursor=cursor)["search"]
        except FetchError as exc:
            if not found or exc.kind != "deadline":
                raise
            reason = str(exc)
            break
        total = max(total, integer(page["issueCount"]))
        pages += 1
        nodes = page["nodes"]
        if not isinstance(nodes, list):
            raise FetchError("Incomplete GitHub response.")
        if len(nodes) > 100:
            reason = "GitHub search page entry limit reached; this list is incomplete."
        for pr in nodes[:100]:
            if pr and pr.get("state") == "OPEN" and (pr.get("author") or {}).get("login", "").lower() == login.lower():
                try:
                    source = source_record(pr)
                except (FetchError, KeyError, TypeError, ValueError):
                    reason = "Some PRs have invalid or oversized fields; this list is incomplete."
                    continue
                found[source["id"]] = source
                if len(found) >= MAX_PRS:
                    break
        cursor = next_cursor(page, cursor)
        if cursor is None or pages >= MAX_SEARCH_PAGES or len(found) >= MAX_PRS:
            break
    if total > MAX_PRS or cursor is not None:
        reason = reason or "PR discovery limit reached (100 PRs / 10 pages); this list is incomplete."
    return sorted(found.values(), key=lambda pr: pr["updatedAt"], reverse=True), reason


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
    pr = source_record(pr)
    return {
        "id": pr["id"], "number": pr["number"], "title": pr["title"], "url": pr["url"],
        "repository": pr["repository"]["nameWithOwner"], "author": pr["author"]["login"],
        "updatedAt": pr["updatedAt"], "draft": pr["isDraft"], "review": "Unknown",
        "discussion": None, "inline": None, "checks": None, "counts": None,
        "error": "Details unavailable", "fetchedAt": None,
    }


def review_pages(api, pr):
    api = budget_api(api)
    connection, seen, count, cursor = pr["reviews"], set(), 0, None
    pages, entries = 0, 0
    while True:
        pages += 1
        nodes = connection["nodes"]
        if not isinstance(nodes, list) or len(nodes) > 100 or entries + len(nodes) > MAX_ENTRIES:
            raise LimitError("Review entry limit reached; inline count is unavailable.")
        entries += len(nodes)
        for review in nodes:
            identifier = reference(review["id"])
            reference(review["state"], MAX_STATUS)
            if identifier not in seen and review["state"] != "PENDING":
                # Every inline reply belongs to a review; totalCount avoids downloading bodies.
                count += integer(review["comments"]["totalCount"])
                seen.add(identifier)
        cursor = next_cursor(connection, cursor)
        if cursor is None:
            return count
        if pages >= MAX_DETAIL_PAGES or entries >= MAX_ENTRIES:
            raise LimitError("Review pagination limit reached; inline count is unavailable.")
        data = api.graphql("query($id:ID!, $cursor:String!) { node(id:$id) { ... on PullRequest { reviews(first:100, after:$cursor) { " + REVIEWS + " } } } }", id=pr["id"], cursor=cursor)
        connection = data["node"]["reviews"]


def check_pages(api, pr):
    api = budget_api(api)
    commits = pr["commits"]["nodes"]
    if not commits:
        raise FetchError("PR head commit is unavailable.")
    commit = commits[-1]["commit"]
    reference(commit["oid"])
    rollup = commit["statusCheckRollup"]
    if rollup is None:
        return []
    connection, found, cursor = rollup["contexts"], {}, None
    pages, entries = 0, 0
    while True:
        pages += 1
        nodes = connection["nodes"]
        if not isinstance(nodes, list) or len(nodes) > 100 or entries + len(nodes) > MAX_ENTRIES:
            raise LimitError("Check entry limit reached; checks are unavailable.")
        entries += len(nodes)
        for check in nodes:
            if check:
                identifier = reference(check["id"])
                name = check.get("name", check.get("context", "Check"))
                status = check.get("conclusion") or check.get("status") or check.get("state") or "UNKNOWN"
                found[identifier] = {
                    "name": display_text(name),
                    "status": display_text(status, MAX_STATUS),
                    "bucket": bucket(check),
                    "_truncated": len(name) > MAX_TEXT or len(status) > MAX_STATUS,
                }
        cursor = next_cursor(connection, cursor)
        if cursor is None:
            return list(found.values())
        if pages >= MAX_DETAIL_PAGES or entries >= MAX_ENTRIES:
            raise LimitError("Check pagination limit reached; checks are unavailable.")
        # Pin subsequent pages to the original commit, even if a push races this fetch.
        owner, name = pr["repository"]["nameWithOwner"].split("/", 1)
        data = api.graphql("query($owner:String!, $name:String!, $oid:GitObjectID!, $cursor:String!) { repository(owner:$owner,name:$name) { object(oid:$oid) { ... on Commit { statusCheckRollup { contexts(first:100,after:$cursor) { " + CHECKS + " } } } } } }", owner=owner, name=name, oid=commit["oid"], cursor=cursor)
        connection = data["repository"]["object"]["statusCheckRollup"]["contexts"]


def stack_info(entry):
    if entry is None:
        return None
    stack = entry["stack"]
    result = {"number": integer(stack["number"]), "size": integer(stack["size"]),
              "position": integer(entry["position"])}
    if not result["number"] or not 1 <= result["position"] <= result["size"]:
        raise ValueError("Invalid stack position")
    return result


def group_stacks(rows):
    """Newest group first; GitHub's bottom-to-top positions within each stack."""
    groups = {}
    for row in sorted(rows, key=lambda row: row["updatedAt"], reverse=True):
        stack = row.get("stack")
        key = (row["repository"], stack["number"]) if stack else (row["id"],)
        groups.setdefault(key, []).append(row)
    return [row for group in groups.values()
            for row in sorted(group, key=lambda row: (row.get("stack") or {}).get("position", 0))]


def normalize(api, pr):
    api = budget_api(api)
    row = basic_row(pr)
    checks = check_pages(api, pr)
    truncated = len(pr["title"]) > MAX_TEXT
    for check in checks:
        truncated = check.pop("_truncated", False) or truncated
    counts = dict.fromkeys(BUCKETS, 0)
    for check in checks:
        counts[check["bucket"]] += 1
    row.update(
        review={"APPROVED": "Approved", "CHANGES_REQUESTED": "Changes requested", "REVIEW_REQUIRED": "Review required", None: "No review decision"}.get(pr["reviewDecision"], "Unknown"),
        stack=stack_info(pr.get("stackEntry")),
        mergeable=reference(pr.get("mergeable", "UNKNOWN"), MAX_STATUS),
        mergeStateStatus=reference(pr.get("mergeStateStatus", "UNKNOWN"), MAX_STATUS),
        discussion=integer(pr["comments"]["totalCount"]), inline=review_pages(api, pr),
        checks=checks, counts=counts, error="Display text shortened to the supported limit." if truncated else None, fetchedAt=time.time(),
    )
    return row


def snapshot(api, login, previous):
    api = budget_api(api)
    previous = safe_previous(previous)
    discovered, truncated = discover(api, login)
    rows, errors = [], []
    old = {pr["id"]: pr for pr in previous.get("prs", [])}
    retry, stopped = 0, None
    for start in range(0, len(discovered), 20):
        batch = discovered[start:start + 20]
        batch_error = None
        try:
            if stopped:
                raise stopped
            nodes = api.graphql(DETAILS, ids=[pr["id"] for pr in batch])["nodes"]
            if not isinstance(nodes, list) or len(nodes) > len(batch):
                raise LimitError("PR detail response entry limit reached.")
            details = {reference(pr["id"]): pr for pr in nodes if pr}
        except FetchError as exc:
            details, batch_error = {}, exc
            if exc.kind == "deadline":
                stopped = exc
        except (KeyError, TypeError, ValueError, AttributeError):
            details, batch_error = {}, FetchError("Incomplete GitHub response.")
        for source in batch:
            try:
                if stopped:
                    raise stopped
                if retry:
                    raise FetchError("Refresh paused: GitHub rate limit reached.", "rate_limit", retry)
                if batch_error:
                    raise batch_error
                pr = details.get(source["id"])
                if not pr:
                    raise FetchError("PR details are unavailable.")
                if pr["state"] != "OPEN" or (pr.get("author") or {}).get("login", "").lower() != login.lower():
                    continue
                row = normalize(api, pr)
                rows.append(row)
                if row["error"]:
                    errors.append(row["error"])
            except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
                if isinstance(exc, FetchError) and exc.kind == "auth":
                    raise exc
                if isinstance(exc, FetchError) and exc.kind == "deadline":
                    stopped = exc
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
        error = truncated + " " + error
    return {
        "schemaVersion": 1, "account": login, "prs": sorted(rows, key=lambda row: row["updatedAt"], reverse=True),
        "partial": bool(errors or truncated), "stale": bool(errors or truncated), "error": error,
        "fetchedAt": now, "lastSuccessAt": previous.get("lastSuccessAt") if errors or truncated else now,
        "retryAt": now + retry if retry else 0,
    }


def integer(value):
    if type(value) is not int or not 0 <= value <= 2 ** 53 - 1:
        raise ValueError("Invalid count")
    return value


def boolean(value):
    if type(value) is not bool:
        raise ValueError("Invalid boolean")
    return value


def validate_snapshot(value):
    """Reject old/untrusted cache shapes before copying or passing them to QML."""
    def text(value, maximum, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError("Invalid string")

    def timestamp(value):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2 ** 53 - 1):
            raise ValueError("Invalid timestamp")

    fields = {"schemaVersion", "account", "prs", "partial", "stale", "error", "fetchedAt",
              "lastSuccessAt", "retryAt", "attemptedAt", "authFingerprint", "failures"}
    if not isinstance(value, dict) or set(value) - fields or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("Invalid snapshot")
    for key in ("account", "authFingerprint"):
        if key in value:
            text(value[key], MAX_NAME)
    text(value.get("error", ""), MAX_TEXT)
    for key in ("partial", "stale"):
        if key in value:
            boolean(value[key])
    for key in ("fetchedAt", "lastSuccessAt", "retryAt", "attemptedAt"):
        if key in value:
            if key in {"retryAt", "attemptedAt"} and value[key] is None:
                raise ValueError("Invalid timestamp")
            timestamp(value[key])
    if "failures" in value:
        integer(value["failures"])
    rows = value.get("prs")
    if not isinstance(rows, list) or len(rows) > MAX_PRS:
        raise ValueError("Invalid PR list")
    row_fields = {"id", "number", "title", "url", "repository", "author", "updatedAt", "draft",
                  "review", "discussion", "inline", "checks", "counts", "error", "fetchedAt"}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row_fields <= set(row) or set(row) - row_fields - {"mergeable", "mergeStateStatus", "awaitingReady", "stack"}:
            raise ValueError("Invalid PR")
        for key, maximum in (("id", MAX_REFERENCE), ("url", MAX_REFERENCE), ("title", MAX_TEXT),
                             ("repository", MAX_NAME), ("author", MAX_NAME), ("updatedAt", MAX_STATUS),
                             ("review", MAX_STATUS)):
            text(row[key], maximum)
        for key in ("mergeable", "mergeStateStatus"):
            if key in row:
                text(row[key], MAX_STATUS)
        if row.get("stack") is not None:
            stack = row["stack"]
            if not isinstance(stack, dict) or set(stack) != {"number", "size", "position"}:
                raise ValueError("Invalid stack")
            stack_info({"stack": stack, "position": stack["position"]})
        if "awaitingReady" in row:
            boolean(row["awaitingReady"])
        if not row["id"] or row["id"] in seen:
            raise ValueError("Invalid PR identifier")
        seen.add(row["id"])
        text(row["error"], MAX_TEXT, nullable=True)
        integer(row["number"])
        boolean(row["draft"])
        timestamp(row["fetchedAt"])
        for key in ("discussion", "inline"):
            if row[key] is not None:
                integer(row[key])
        checks, counts = row["checks"], row["counts"]
        if checks is None:
            if counts is not None:
                raise ValueError("Counts without checks")
            continue
        if not isinstance(checks, list) or len(checks) > MAX_ENTRIES:
            raise ValueError("Invalid checks")
        expected = dict.fromkeys(BUCKETS, 0)
        for check in checks:
            if not isinstance(check, dict) or set(check) != {"name", "status", "bucket"}:
                raise ValueError("Invalid check")
            text(check["name"], MAX_TEXT)
            text(check["status"], MAX_STATUS)
            text(check["bucket"], MAX_STATUS)
            if check["bucket"] not in expected:
                raise ValueError("Invalid check bucket")
            expected[check["bucket"]] += 1
        if not isinstance(counts, dict) or set(counts) != set(BUCKETS):
            raise ValueError("Invalid counts")
        for count in counts.values():
            integer(count)
        if counts != expected:
            raise ValueError("Check counts disagree")
    return value


def safe_previous(value):
    try:
        return validate_snapshot(dict({"schemaVersion": 1, "prs": []}, **value))
    except (ValueError, TypeError, AttributeError):
        return {}


def encoded_snapshot(value, public=False):
    fields = {key: item for key, item in value.items() if not public or key != "authFingerprint"}
    buffer = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(fields):
        encoded = chunk.encode("utf-8")
        if len(buffer) + len(encoded) + 1 > MAX_SNAPSHOT_BYTES:
            raise LimitError("Snapshot size limit reached.")
        buffer.extend(encoded)
    buffer.extend(b"\n")
    return bytes(buffer)


def finalize(value, last_success=None):
    """The same bounded representation feeds the cache and stdout on every path."""
    try:
        result = copy.deepcopy(validate_snapshot(value))
    except (ValueError, TypeError, AttributeError):
        result = {"schemaVersion": 1, "prs": [], "partial": True, "stale": True,
                  "error": "Invalid or oversized PR snapshot; refresh to try again."}
    result["prs"].sort(key=lambda row: row["updatedAt"], reverse=True)
    try:
        encoded_snapshot(result)
    except LimitError:
        result.update(partial=True, stale=True, lastSuccessAt=last_success,
                      error="Snapshot size limit reached; oldest PRs were omitted.")
        # Find the longest newest-first prefix without repeatedly encoding a huge tail.
        rows = result["prs"]
        low, high = 0, len(rows)
        while low < high:
            middle = (low + high + 1) // 2
            result["prs"] = rows[:middle]
            try:
                encoded_snapshot(result)
                low = middle
            except LimitError:
                high = middle - 1
        result["prs"] = rows[:low]
    result["prs"] = group_stacks(result["prs"])
    return result


class Cache:
    def __init__(self, path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise FetchError("Cache directory must be owned by you and must not be a symlink.", "cache")
        os.chmod(path, 0o700)
        self.path = path
        self.fd = None

    def lock(self, deadline):
        self.fd = os.open(self.path / "refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        while True:
            deadline.remaining()
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                time.sleep(min(0.05, deadline.remaining()))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self):
        try:
            fd = os.open(self.path / "snapshot.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return {}
                raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                return {}
            return validate_snapshot(json.loads(raw))
        except (OSError, ValueError, AttributeError, TypeError, RecursionError):
            return {}

    def write(self, data):
        data = finalize(data, data.get("lastSuccessAt"))
        name = self.path / f"snapshot.{os.getpid()}.tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded_snapshot(data))
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


def ready_notifications(result, previous, enabled):
    """Persist review cycles in the locked cache, including while CI is pending."""
    old = {row["id"]: row for row in previous.get("prs", [])}
    notifications = []
    for row in result["prs"]:
        before = old.get(row["id"], {})
        awaiting = before.get("awaitingReady", before.get("review") in {"Review required", "Changes requested"})
        if not row.get("error"):
            if row["review"] in {"Review required", "Changes requested"}:
                awaiting = True
            counts = row.get("counts")
            ready = (not result.get("stale") and not result.get("partial")
                     and row["review"] == "Approved" and not row["draft"]
                     and row.get("mergeable") == "MERGEABLE"
                     and row.get("mergeStateStatus") == "CLEAN"
                     and counts is not None
                     and not any(counts[key] for key in ("running", "failed", "unknown")))
            if enabled and awaiting and ready:
                notifications.append(row)
                awaiting = False
        if awaiting or "awaitingReady" in before:
            row["awaitingReady"] = awaiting
    return notifications


def notify_ready(row, deadline):
    """Best-effort local notification; never let a desktop failure break fetching."""
    try:
        subprocess.run(
            ["notify-send", "--app-name=GitHub PR Status", "--icon=git-pull-request",
             "--", "Pull request ready to merge",
             html.escape(f'{row["repository"]} #{row["number"]}: {row["title"]}')],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=min(2, deadline.remaining()), check=False,
        )
    except (OSError, subprocess.TimeoutExpired, FetchError):
        pass


def run(api, cache, interval=60, force=False, now=time.time, monotonic=time.monotonic, notify=False):
    deadline = Deadline(monotonic)
    api = BudgetAPI(api, deadline)
    try:
        try:
            cache.lock(deadline)
        except FetchError as exc:
            previous = cache.read()
            if previous.get("authFingerprint") != auth_fingerprint():
                previous = {}
            result = copy.deepcopy(previous)
            result.update(schemaVersion=1, prs=previous.get("prs", []), partial=True, stale=True, error=str(exc))
            # Another process owns the lock; do not overwrite its snapshot.
            return finalize(result, previous.get("lastSuccessAt"))
        previous = cache.read()
        fingerprint = auth_fingerprint()
        if previous.get("authFingerprint") != fingerprint:
            previous = {}
        timestamp = now()
        age = timestamp - previous.get("attemptedAt", 0)
        # Coalesce simultaneous manual requests and respect server backoff even on manual refresh.
        if previous and (previous.get("retryAt", 0) > timestamp or 0 <= age < (3 if force else interval)):
            return finalize(previous, previous.get("lastSuccessAt"))
        try:
            login = reference(api.graphql("query { viewer { login } }")["viewer"]["login"], MAX_NAME)
            if previous.get("account") != login:
                previous = {}
            result = snapshot(api, login, previous)
        except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
            auth = isinstance(exc, FetchError) and exc.kind == "auth"
            result = {} if auth else copy.deepcopy(previous)
            result.update(schemaVersion=1, stale=True, partial=result.get("partial", False) or getattr(exc, "kind", "") in {"limit", "deadline"},
                          error=str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response.")
            result.setdefault("prs", [])
            result["failures"] = min(previous.get("failures", 0) + 1, 1000)
            result["retryAt"] = timestamp + max(getattr(exc, "retry_after", 0), min(900, 60 * 2 ** min(result["failures"] - 1, 4)))
        result.update(attemptedAt=timestamp, authFingerprint=fingerprint)
        result = finalize(result, previous.get("lastSuccessAt"))
        notifications = ready_notifications(result, previous, notify)
        result = finalize(result, previous.get("lastSuccessAt"))
        # Claim delivery before sending while holding the shared lock: no duplicates
        # across monitors or restarts, even if the desktop notification service fails.
        cache.write(result)
        retained = {row["id"] for row in result["prs"]}
        for row in notifications:
            if row["id"] in retained and not result.get("stale"):
                notify_ready(row, deadline)
        return result
    finally:
        cache.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, choices=(20, 60), default=60)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--notify", action="store_true", help="Notify when a previously review-blocked PR is ready to merge")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        path = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "omarchy-github-pr-status"
        result = run(GitHub(), Cache(path), args.interval, args.force, notify=args.notify)
    except (OSError, FetchError) as exc:
        result = {"schemaVersion": 1, "prs": [], "stale": True, "error": "Cannot use PR cache. Check cache directory permissions."}
    # The fingerprint is only for internal cache invalidation.
    import sys
    sys.stdout.buffer.write(encoded_snapshot(finalize(result, result.get("lastSuccessAt")), public=True))


if __name__ == "__main__":
    main()
