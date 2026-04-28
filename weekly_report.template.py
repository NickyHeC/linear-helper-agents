"""Weekly report — Linear issues with status changes this week.

Pulls issues assigned to a given user that were newly created or had a
status change between Monday of the target week and the end of that week.
Exports a concise markdown report grouped by status transition (Done first)
then newly created issues at the end.

Copy this file to weekly_report.py and update DEFAULT_ASSIGNEE.

Usage:
    python weekly_report.py
    python weekly_report.py --assignee "someone@company.com"
    python weekly_report.py --week-of 2026-03-23
"""

import asyncio
import os
import re
import sys
import webbrowser
from datetime import datetime, timedelta

from dedalus_labs import AsyncDedalus, AuthenticationError, DedalusRunner
from dotenv import load_dotenv


load_dotenv()

from connection import linear_secrets

LINEAR_MCP_SERVER = os.getenv("LINEAR_MCP_SERVER")
MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "600"))

DEFAULT_ASSIGNEE = os.getenv("LINEAR_ASSIGNEE", "me")

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekly_report")


def get_monday(week_of: str | None = None) -> str:
    if week_of:
        target = datetime.strptime(week_of, "%Y-%m-%d")
    else:
        target = datetime.now()
    monday = target - timedelta(days=target.weekday())
    return monday.strftime("%Y-%m-%d")


def build_prompt(assignee: str, monday: str, today: str) -> str:
    return f"""You are a Linear workspace reporter. Generate a concise weekly status report.

Today's date: {today}
Report window: {monday} (Monday) through {today} (now)
Assignee: {assignee}

## Instructions — follow every step precisely

### Step 1: Fetch issues created this week
Call `linear_list_issues` with:
- assignee: "{assignee}"
- createdAt: "{monday}T00:00:00Z"
- limit: 250
- orderBy: "createdAt"

Record ALL results. If there is a next-page cursor, paginate until done.

### Step 2: Fetch issues updated this week
Call `linear_list_issues` with:
- assignee: "{assignee}"
- updatedAt: "{monday}T00:00:00Z"
- limit: 250
- orderBy: "updatedAt"

Record ALL results. Paginate if needed.

### Step 3: Merge and deduplicate
Combine both result sets. Remove duplicates (same issue ID).

### Step 4: Filter by date window
Keep only issues where:
- createdAt is between {monday}T00:00:00Z and now, OR
- updatedAt is between {monday}T00:00:00Z and now

### Step 5: Classify each issue

For each issue, determine if it had a STATUS CHANGE or was NEWLY CREATED:

**Newly Created**: The issue's createdAt falls within the window ({monday} to {today}).
This includes ALL issues created in the window regardless of current status.

**Status Changed**: An issue counts as "status changed" if ANY of these are true:
- It was created BEFORE {monday} but updatedAt is within the window (pre-existing issue that was updated)
- It was created within the window AND its current status is NOT "Todo" and NOT "Backlog" (meaning it moved beyond its initial state)
- It has a completedAt timestamp within the window

An issue can appear in BOTH the Status Changed and Newly Created sections.
If it was created in the window AND also changed status, it appears in BOTH sections.

### Step 6: Count the numbers for the summary

Before generating the report, count:
- **Completed**: number of status-changed issues whose current status is Done or In Review
- **Status Updated**: total number of ALL issues that had a status change in the window (this is the total count of everything in the Status Changes section)
- **In Progress**: number of status-changed issues whose current status is In Progress
- **Newly Created**: total number of issues created within the window (ALL of them, regardless of status)

### Step 7: Output the report

Output EXACTLY this markdown format and nothing else.
Do NOT output any thinking, planning, reasoning, or intermediate work before the report.
The very first characters of your output MUST be the `# Weekly Report` heading.

# Weekly Report — {assignee}
**Week of {monday}**

## Overview
| Metric | Count |
|--------|-------|
| Completed (Done + In Review) | [N] |
| In Progress | [N] |
| Status Updated | [N] |
| Newly Created | [N] |

## Status Changes

Group the status-changed issues by their CURRENT status in this exact order:
Done, then In Review, then In Progress, then Todo, then Backlog, then Canceled.

For each status group, output a heading and a table.
Only include groups that have at least one issue.

For issues created BEFORE the window, show the transition as "→ [current status]".
For issues created WITHIN the window that also changed status, show "New → [current status]".

### Done
| Issue | Transition |
|-------|------------|
| [ENG-123](https://linear.app/...) Title here | → Done |
| [ENG-456](https://linear.app/...) Another title | New → Done |

### In Review
| Issue | Transition |
|-------|------------|
| ... | ... |

(continue for each status group that has issues)

## Newly Created
| Issue | Status |
|-------|--------|
| [ENG-789](https://linear.app/...) Title here | Todo |
| [ENG-456](https://linear.app/...) Another title | Done |

List ALL issues created within the window here, regardless of their current status.
Include issues that also appear in the Status Changes section above.

## Critical rules
- The issue ID (e.g. ENG-123) MUST be a markdown link to the issue's Linear URL.
- Format: [ISSUE-ID](url) Title
- Status Changed section comes first, Newly Created section comes last.
- Within Status Changed, group by current status in this order: Done, In Review, In Progress, Todo, Backlog, Canceled.
- The Newly Created section lists ALL issues created in the window, even if they also had status changes.
- Only show issues that match the assignee and date window.
- Do NOT add any commentary, thinking, or reasoning outside the report format.
- The FIRST line of your output MUST be `# Weekly Report — {assignee}`. No preamble.
- Output ONLY the markdown report, nothing else."""


def extract_report(raw: str) -> str:
    """Strip agent thinking/logs and return only the markdown report."""
    match = re.search(r"(# Weekly Report .+)", raw, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"
    return raw.strip() + "\n"


def _extract_connect_url(err: AuthenticationError) -> str | None:
    body = err.body if isinstance(err.body, dict) else {}
    return body.get("connect_url") or body.get("detail", {}).get("connect_url")


def _prompt_oauth(url: str) -> None:
    print("\nLinear OAuth required. Opening browser...")
    print(f"   If browser doesn't open, visit:\n   {url}")
    webbrowser.open(url)
    input("\n   Press Enter after completing OAuth...")


async def run_report(assignee: str, week_of: str | None = None) -> str:
    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)

    monday = get_monday(week_of)
    if week_of:
        end_date = (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    else:
        end_date = datetime.now().strftime("%Y-%m-%d")
    today = end_date
    prompt = build_prompt(assignee, monday, today)

    print(f"Weekly Report Generator")
    print(f"{'=' * 50}")
    print(f"  Assignee: {assignee}")
    print(f"  Window:   {monday} → {today}")
    print(f"  Model:    {MODEL}")
    print(f"  MCP:      {LINEAR_MCP_SERVER}")
    print(f"{'=' * 50}\n")

    async def execute():
        response = runner.run(
            input=prompt,
            model=MODEL,
            mcp_servers=[LINEAR_MCP_SERVER],
            credentials=[linear_secrets.to_dict()],
            stream=True,
        )
        output = ""
        async for chunk in response:
            if hasattr(chunk, "choices"):
                for choice in chunk.choices:
                    delta = getattr(choice, "delta", None)
                    if delta and hasattr(delta, "content") and delta.content:
                        print(delta.content, end="", flush=True)
                        output += delta.content
        return output

    try:
        report = await execute()
    except AuthenticationError as err:
        url = _extract_connect_url(err)
        if not url:
            raise
        _prompt_oauth(url)
        report = await execute()

    print()
    return report


async def main() -> None:
    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See env.example.")
        sys.exit(1)
    if not LINEAR_MCP_SERVER:
        print("Error: LINEAR_MCP_SERVER not set. See env.example.")
        sys.exit(1)

    assignee = DEFAULT_ASSIGNEE
    week_of = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--assignee" and i < len(sys.argv) - 1:
            assignee = sys.argv[i + 1]
        elif arg == "--week-of" and i < len(sys.argv) - 1:
            week_of = sys.argv[i + 1]

    raw = await run_report(assignee, week_of=week_of)
    report = extract_report(raw)

    if report.strip():
        monday = get_monday(week_of)
        month_day = datetime.strptime(monday, "%Y-%m-%d").strftime("%m-%d")
        os.makedirs(REPORT_DIR, exist_ok=True)
        filename = os.path.join(REPORT_DIR, f"week_{month_day}.md")
        with open(filename, "w") as f:
            f.write(report)
        print(f"\nReport saved to: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
