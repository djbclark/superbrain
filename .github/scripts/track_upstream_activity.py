#!/usr/bin/env python3
"""Track upstream SuperBrain pull requests/issues and emit Telegram-ready nudges."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


API_ROOT = "https://api.github.com"
ROADMAP_URL = "https://github.com/djbclark/superbrain/issues/3"
SELF_LOGINS = {"djbclark"}
BIOME_BRANCH = "prep/biome-tooling"
STALE_AFTER = timedelta(days=3)
STALE_REPEAT = timedelta(days=3)
ISSUE_STALE_AFTER = timedelta(days=7)
ISSUE_STALE_REPEAT = timedelta(days=7)
TRACKED_PULLS = (
    {
        "repo": "sidinsearch/superbrain",
        "number": 4,
        "label": "Live API probe isolation",
    },
    {
        "repo": "sidinsearch/superbrain",
        "number": 5,
        "label": "Mobile delta-sync pagination",
    },
)
TRACKED_ISSUES = (
    {
        "repo": "sidinsearch/superbrain",
        "number": 6,
        "label": "YouTube subscription organization proposal",
    },
)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token

    def get(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{API_ROOT}{path}",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def get_all(self, path: str) -> list[dict[str, Any]]:
        separator = "&" if "?" in path else "?"
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise RuntimeError(f"Expected a list from GitHub API path {path}")
            results.extend(batch)
            if len(batch) < 100:
                return results
            page += 1

    def exists(self, path: str) -> bool:
        try:
            self.get(path)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "superbrain-upstream-activity-tracker",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def event_record(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    author = item.get("user") or {}
    timestamp = item.get("updated_at") or item.get("submitted_at") or item.get("created_at") or ""
    fingerprint_source = "\x00".join(
        [
            timestamp,
            str(item.get("state") or ""),
            str(item.get("body") or ""),
        ]
    )
    return {
        "kind": kind,
        "author": author.get("login") or "unknown",
        "author_type": author.get("type") or "",
        "url": item.get("html_url") or "",
        "timestamp": timestamp,
        "state": str(item.get("state") or "").lower(),
        "fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
    }


def collect_pull(client: GitHubClient, spec: dict[str, Any]) -> dict[str, Any]:
    repo = spec["repo"]
    number = spec["number"]
    pull = client.get(f"/repos/{repo}/pulls/{number}")
    issue_comments = client.get_all(f"/repos/{repo}/issues/{number}/comments")
    reviews = client.get_all(f"/repos/{repo}/pulls/{number}/reviews")
    review_comments = client.get_all(f"/repos/{repo}/pulls/{number}/comments")

    events: dict[str, dict[str, Any]] = {}
    for kind, items in (
        ("comment", issue_comments),
        ("review", reviews),
        ("review comment", review_comments),
    ):
        for item in items:
            events[f"{kind}:{item['id']}"] = event_record(kind, item)

    return {
        "repo": repo,
        "number": number,
        "label": spec["label"],
        "title": pull.get("title") or spec["label"],
        "url": pull.get("html_url") or f"https://github.com/{repo}/pull/{number}",
        "state": pull.get("state") or "unknown",
        "draft": bool(pull.get("draft")),
        "merged": bool(pull.get("merged_at")),
        "updated_at": pull.get("updated_at") or "",
        "events": events,
    }


def collect_issue(client: GitHubClient, spec: dict[str, Any]) -> dict[str, Any]:
    repo = spec["repo"]
    number = spec["number"]
    issue = client.get(f"/repos/{repo}/issues/{number}")
    comments = client.get_all(f"/repos/{repo}/issues/{number}/comments")
    events = {
        f"comment:{item['id']}": event_record("comment", item) for item in comments
    }
    return {
        "repo": repo,
        "number": number,
        "label": spec["label"],
        "title": issue.get("title") or spec["label"],
        "url": issue.get("html_url") or f"https://github.com/{repo}/issues/{number}",
        "state": issue.get("state") or "unknown",
        "updated_at": issue.get("updated_at") or "",
        "events": events,
    }


def collect_snapshot(client: GitHubClient) -> dict[str, Any]:
    encoded_branch = urllib.parse.quote(BIOME_BRANCH, safe="")
    biome_branch_ready = client.exists(
        f"/repos/djbclark/superbrain/branches/{encoded_branch}"
    )
    biome_pulls = client.get(
        "/repos/sidinsearch/superbrain/pulls"
        "?state=all&head=djbclark%3Aprep%2Fbiome-tooling&sort=created&direction=desc&per_page=1"
    )
    pulls = [collect_pull(client, spec) for spec in TRACKED_PULLS]
    if biome_pulls:
        pulls.append(
            collect_pull(
                client,
                {
                    "repo": "sidinsearch/superbrain",
                    "number": biome_pulls[0]["number"],
                    "label": "Biome tooling baseline",
                },
            )
        )
    return {
        "version": 1,
        "collected_at": utcnow().isoformat(),
        "pulls": pulls,
        "issues": [collect_issue(client, spec) for spec in TRACKED_ISSUES],
        "biome_branch_ready": biome_branch_ready,
        "biome_pr_open": bool(biome_pulls and biome_pulls[0]["state"] == "open"),
        "meta": {},
    }


def latest_review_states(pull: dict[str, Any]) -> dict[str, str]:
    latest: dict[str, tuple[str, str]] = {}
    for event in pull["events"].values():
        if event["kind"] != "review" or event["author"] in SELF_LOGINS:
            continue
        timestamp = event["timestamp"]
        author = event["author"]
        if author not in latest or timestamp > latest[author][0]:
            latest[author] = (timestamp, event["state"])
    return {author: state for author, (_, state) in latest.items()}


def status_word(pull: dict[str, Any]) -> str:
    if pull["merged"]:
        return "merged"
    if pull["state"] == "closed":
        return "closed"
    if pull["draft"]:
        return "draft"
    return pull["state"]


def compare_snapshots(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    now: datetime,
    force: bool,
    scheduled: bool,
) -> tuple[bool, str]:
    changes: list[str] = []
    external_activity = False
    previous_pulls = {
        pull["number"]: pull for pull in (previous or {}).get("pulls", [])
    }
    previous_issues = {
        issue["number"]: issue for issue in (previous or {}).get("issues", [])
    }
    previous_meta = (previous or {}).get("meta") or {}
    current_meta = {
        "last_action_key": previous_meta.get("last_action_key", "waiting"),
        "last_stale_nudges": dict(previous_meta.get("last_stale_nudges") or {}),
        "last_issue_stale_nudges": dict(
            previous_meta.get("last_issue_stale_nudges") or {}
        ),
    }

    if previous:
        for pull in current["pulls"]:
            old = previous_pulls.get(pull["number"])
            if not old:
                changes.append(f"➕ Now tracking PR #{pull['number']}")
                continue

            old_status = status_word(old)
            new_status = status_word(pull)
            if old_status != new_status:
                changes.append(
                    f"🔄 PR #{pull['number']} changed: {old_status} → {new_status}"
                )

            old_events = old.get("events") or {}
            for event_id, event in pull["events"].items():
                if event["author"] in SELF_LOGINS:
                    continue
                old_event = old_events.get(event_id)
                if old_event is None:
                    external_activity = True
                    detail = event["kind"]
                    if event["kind"] == "review" and event["state"]:
                        detail = f"{event['state'].replace('_', ' ')} review"
                    changes.append(
                        f"💬 PR #{pull['number']}: new {detail} by @{event['author']}"
                    )
                elif old_event.get("fingerprint") != event["fingerprint"]:
                    external_activity = True
                    changes.append(
                        f"✏️ PR #{pull['number']}: {event['kind']} edited by @{event['author']}"
                    )

        for issue in current["issues"]:
            old = previous_issues.get(issue["number"])
            if not old:
                changes.append(f"➕ Now tracking upstream issue #{issue['number']}")
                continue

            if old["state"] != issue["state"]:
                external_activity = True
                changes.append(
                    f"🔄 Issue #{issue['number']} changed: "
                    f"{old['state']} → {issue['state']}"
                )

            old_events = old.get("events") or {}
            for event_id, event in issue["events"].items():
                if event["author"] in SELF_LOGINS:
                    continue
                old_event = old_events.get(event_id)
                if old_event is None:
                    external_activity = True
                    changes.append(
                        f"💬 Issue #{issue['number']}: new comment by @{event['author']}"
                    )
                elif old_event.get("fingerprint") != event["fingerprint"]:
                    external_activity = True
                    changes.append(
                        f"✏️ Issue #{issue['number']}: comment edited by @{event['author']}"
                    )

    changes_requested: list[int] = []
    for pull in current["pulls"]:
        if "changes_requested" in latest_review_states(pull).values():
            changes_requested.append(pull["number"])

    open_pulls = [
        pull for pull in current["pulls"] if pull["state"] == "open" and not pull["merged"]
    ]
    foundation_numbers = {spec["number"] for spec in TRACKED_PULLS}
    open_foundation_pulls = [
        pull for pull in open_pulls if pull["number"] in foundation_numbers
    ]
    open_issues = [issue for issue in current["issues"] if issue["state"] == "open"]
    if changes_requested:
        joined = ", ".join(f"#{number}" for number in changes_requested)
        action_key = f"changes-requested:{joined}"
        next_action = f"Review requested changes and reply on {joined}."
    elif external_activity:
        action_key = "review-new-upstream-activity"
        next_action = "Review the new upstream activity and reply if needed."
    elif (
        len(open_foundation_pulls) < len(TRACKED_PULLS)
        and current["biome_branch_ready"]
        and not current["biome_pr_open"]
    ):
        action_key = "open-biome-tooling-pr"
        next_action = "A PR slot is open: rebase and submit prep/biome-tooling."
    else:
        action_key = "waiting"
        if open_pulls and open_issues:
            pull_numbers = ", ".join(f"#{pull['number']}" for pull in open_pulls)
            issue_numbers = ", ".join(f"#{issue['number']}" for issue in open_issues)
            next_action = (
                f"Wait for upstream review on PRs {pull_numbers} "
                f"and proposal {issue_numbers}."
            )
        elif open_pulls:
            pull_numbers = ", ".join(f"#{pull['number']}" for pull in open_pulls)
            next_action = f"Wait for upstream review on open PRs {pull_numbers}."
        elif open_issues:
            issue_numbers = ", ".join(f"#{issue['number']}" for issue in open_issues)
            next_action = f"Wait for upstream feedback on proposal {issue_numbers}."
        else:
            next_action = "Review the contribution roadmap and select the next upstream-safe wave."

    if previous and action_key != previous_meta.get("last_action_key", "waiting"):
        if action_key != "waiting":
            changes.append(f"➡️ Next action changed: {next_action}")
    current_meta["last_action_key"] = action_key

    if scheduled and previous:
        for pull in open_pulls:
            updated_at = parse_timestamp(pull["updated_at"])
            if updated_at is None or now - updated_at < STALE_AFTER:
                continue
            nudge_key = str(pull["number"])
            last_nudge = parse_timestamp(
                current_meta["last_stale_nudges"].get(nudge_key)
            )
            if last_nudge is None or now - last_nudge >= STALE_REPEAT:
                age_days = (now - updated_at).days
                changes.append(
                    f"⏰ PR #{pull['number']} has had no activity for {age_days} days; "
                    "consider a concise follow-up."
                )
                current_meta["last_stale_nudges"][nudge_key] = now.isoformat()

        for issue in open_issues:
            updated_at = parse_timestamp(issue["updated_at"])
            if updated_at is None or now - updated_at < ISSUE_STALE_AFTER:
                continue
            nudge_key = str(issue["number"])
            last_nudge = parse_timestamp(
                current_meta["last_issue_stale_nudges"].get(nudge_key)
            )
            if last_nudge is None or now - last_nudge >= ISSUE_STALE_REPEAT:
                age_days = (now - updated_at).days
                changes.append(
                    f"⏰ Issue #{issue['number']} has had no activity for {age_days} days; "
                    "consider a concise follow-up."
                )
                current_meta["last_issue_stale_nudges"][nudge_key] = now.isoformat()

    current["meta"] = current_meta
    should_notify = bool(changes) or force

    lines = ["🔔 <b>SuperBrain upstream tracker</b>", ""]
    if changes:
        lines.extend(html.escape(change) for change in changes)
        lines.append("")
    elif force:
        lines.extend(["Manual status report; no changes detected.", ""])

    for pull in current["pulls"]:
        status = html.escape(status_word(pull))
        label = html.escape(pull["label"])
        lines.append(
            f'<a href="{html.escape(pull["url"], quote=True)}">PR #{pull["number"]}</a>: '
            f"<b>{status}</b> — {label}"
        )

    for issue in current["issues"]:
        status = html.escape(issue["state"])
        label = html.escape(issue["label"])
        lines.append(
            f'<a href="{html.escape(issue["url"], quote=True)}">Issue #{issue["number"]}</a>: '
            f"<b>{status}</b> — {label}"
        )

    lines.extend(
        [
            "",
            f"<b>Next:</b> {html.escape(next_action)}",
            f'<a href="{ROADMAP_URL}">Open the contribution roadmap</a>',
        ]
    )
    return should_notify, "\n".join(lines)


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def write_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--message-file", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()

    previous = load_state(args.state_file)
    current = collect_snapshot(GitHubClient(os.getenv("GH_TOKEN")))
    notify, message = compare_snapshots(
        previous,
        current,
        utcnow(),
        force=args.force,
        scheduled=args.scheduled,
    )
    save_state(args.state_file, current)
    args.message_file.parent.mkdir(parents=True, exist_ok=True)
    args.message_file.write_text(message + "\n", encoding="utf-8")
    write_output("notify", "true" if notify else "false")
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
