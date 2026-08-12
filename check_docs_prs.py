#!/usr/bin/env python3
"""
Check all open PRs in esphome.io for linked esphome PRs.
Flags docs PRs where the linked esphome PR has been merged.

Only *confirmed* pairs count: a docs PR body mentioning a code PR is not enough,
the code PR has to reference the docs PR back. Docs PR bodies routinely name code
PRs in prose that they do not actually ship alongside, and flagging on those
produced false positives. One-way references are still reported, separately.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from esphomerelease.docs_pr_links import extract_esphome_pr_numbers, is_confirmed_pair


@dataclass
class LinkedPR:
    number: int
    state: str
    merged_at: str | None
    title: str
    body: str = ""


@dataclass
class DocsPR:
    number: int
    title: str
    url: str
    # Code PRs that reference this docs PR back: the genuine pairs.
    confirmed_prs: list[LinkedPR] = field(default_factory=list)
    # Code PRs this docs PR names one-way, kept for reviewer context only.
    unconfirmed_prs: list[LinkedPR] = field(default_factory=list)


def run_gh_command(args: list[str]) -> str:
    """Run a gh CLI command and return the output."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def get_open_docs_prs() -> list[dict]:
    """Get all open PRs from esphome.io repo."""
    output = run_gh_command(
        [
            "pr",
            "list",
            "--repo",
            "esphome/esphome.io",
            "--state",
            "open",
            "--limit",
            "500",
            "--json",
            "number,title,url,body",
        ]
    )
    return json.loads(output)


def get_esphome_pr_state(pr_number: int) -> LinkedPR | None:
    """Get the state of an esphome PR."""
    try:
        output = run_gh_command(
            [
                "pr",
                "view",
                str(pr_number),
                "--repo",
                "esphome/esphome",
                "--json",
                "state,mergedAt,title,body",
            ]
        )
        data = json.loads(output)
        return LinkedPR(
            number=pr_number,
            state=data["state"],
            merged_at=data.get("mergedAt"),
            title=data["title"],
            body=data.get("body") or "",
        )
    except subprocess.CalledProcessError:
        return None


def fetch_linked_pr_states(pr_numbers: list[int]) -> dict[int, LinkedPR | None]:
    """Fetch the state of many esphome PRs concurrently, one gh call each."""
    if not pr_numbers:
        return {}
    with ThreadPoolExecutor(max_workers=min(16, len(pr_numbers))) as pool:
        return dict(zip(pr_numbers, pool.map(get_esphome_pr_state, pr_numbers)))


def _print_linked_pr(linked: LinkedPR) -> None:
    status = "✅ MERGED" if linked.state == "MERGED" else f"⏳ {linked.state}"
    merged_info = f" (merged: {linked.merged_at})" if linked.merged_at else ""
    print(f"     - #{linked.number}: {linked.title}")
    print(f"       Status: {status}{merged_info}")


def main():
    print("Fetching open PRs from esphome.io...")
    docs_prs = get_open_docs_prs()
    print(f"Found {len(docs_prs)} open PRs\n")

    linked_numbers: dict[int, list[int]] = {
        pr["number"]: extract_esphome_pr_numbers(pr.get("body", ""))
        for pr in docs_prs
    }
    # Several docs PRs may reference the same esphome PR; fetch each unique
    # number once, in parallel, instead of serially per reference.
    linked_states = fetch_linked_pr_states(
        sorted({num for nums in linked_numbers.values() for num in nums})
    )

    flagged_prs: list[DocsPR] = []

    for pr in docs_prs:
        pr_numbers = linked_numbers[pr["number"]]

        if not pr_numbers:
            continue

        docs_pr = DocsPR(number=pr["number"], title=pr["title"], url=pr["url"])
        has_merged = False

        for pr_num in pr_numbers:
            linked_pr = linked_states[pr_num]
            if not linked_pr:
                continue
            if is_confirmed_pair(
                docs_body=pr.get("body", ""),
                docs_number=pr["number"],
                code_body=linked_pr.body,
                code_number=pr_num,
            ):
                docs_pr.confirmed_prs.append(linked_pr)
                # Only a confirmed pair means "the code landed, the docs did
                # not". A merged PR this docs PR merely mentions does not.
                if linked_pr.state == "MERGED":
                    has_merged = True
            else:
                docs_pr.unconfirmed_prs.append(linked_pr)

        if has_merged:
            flagged_prs.append(docs_pr)

    if not flagged_prs:
        print("✅ No docs PRs found with merged esphome PRs")
        return 0

    print(f"🚨 Found {len(flagged_prs)} docs PRs with merged esphome PRs:\n")
    print("=" * 80)

    for docs_pr in flagged_prs:
        print(f"\n📄 Docs PR #{docs_pr.number}: {docs_pr.title}")
        print(f"   {docs_pr.url}")
        print("   Linked esphome PRs (confirmed, they link back):")
        for linked in docs_pr.confirmed_prs:
            _print_linked_pr(linked)
        if docs_pr.unconfirmed_prs:
            print("   Mentioned esphome PRs (unconfirmed, no back-link):")
            for linked in docs_pr.unconfirmed_prs:
                _print_linked_pr(linked)

    print("\n" + "=" * 80)
    print(f"\nSummary: {len(flagged_prs)} docs PRs need attention")

    return 1


if __name__ == "__main__":
    sys.exit(main())
