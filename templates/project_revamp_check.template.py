"""Linear Revamp — extract project actions from neglect report and verify against Linear.

Reads the neglect report, extracts project titles and parenthetical actions,
cross-checks each project's actual state in Linear, and writes verified
results to linear_revamp_projects.md.

Copy this file to project_revamp_check.py.

Usage:
    python project_revamp_check.py
    REPORT_PATH=neglect_report.md python project_revamp_check.py
"""

import asyncio
import os
import re
import sys
import webbrowser

from dedalus_labs import AsyncDedalus, AuthenticationError, DedalusRunner
from dotenv import load_dotenv


load_dotenv()

from connection import linear_secrets

LINEAR_MCP_SERVER = os.getenv("LINEAR_MCP_SERVER")
MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "600"))
REPORT_PATH = os.getenv("REPORT_PATH", "neglect_report.md")
TEAM_NAME = os.getenv("LINEAR_TEAM_NAME", "Engineering")


def parse_projects(report_text: str) -> list[dict]:
    """Extract project titles and parenthetical actions from the report."""
    pattern = re.compile(
        r"^### (\d+)\.\s+(.+?)(?:\s*\(([^)]+)\))?\s*$", re.MULTILINE
    )
    projects = []
    for match in pattern.finditer(report_text):
        rank = int(match.group(1))
        title = match.group(2).strip()
        action = match.group(3).strip() if match.group(3) else None
        projects.append({"rank": rank, "title": title, "action": action})
    return projects


def build_prompt(projects: list[dict]) -> str:
    project_list = "\n".join(
        f"{p['rank']}. **{p['title']}** — Noted action: {p['action'] or '(none noted)'}"
        for p in projects
    )

    return f"""You are a Linear workspace auditor. I have a list of projects from the "{TEAM_NAME}" team with actions that were noted during a neglect review. Your job is to verify each action against the ACTUAL current state of these projects in Linear.

## Projects to verify

{project_list}

## Instructions

1. Call `linear_list_teams` to find the "{TEAM_NAME}" team.

2. Call `linear_list_projects` with the team filter to get ALL projects. Use limit=250 and paginate if needed.

3. For EACH project in the list above, check its ACTUAL current state in Linear:
   - Does the project still exist? (If noted as "deleted", is it actually gone?)
   - What is the project's current state? (planned/started/paused/completed/cancelled)
   - What is the progress percentage?
   - When was it last updated?
   - If the noted action says "merged" — can you find the merged-into project?
   - If the noted action says "done" or "completely done" — is the state actually "completed"?
   - If the noted action says "in progress" — is the state actually "started"?
   - If the noted action says "assigned to X" — verify if that person is involved

4. Output EXACTLY this markdown format and nothing else:

# Linear Project Revamp — Verification Report

**Generated:** [today's date]
**Team:** {TEAM_NAME}

## Project Actions Summary

| # | Project | Noted Action | Actual State | Progress | Last Updated | Verified | Notes |
|---|---------|-------------|--------------|----------|-------------|----------|-------|
| 1 | Chat Interface | merged 2 projects, still need planning | [actual state] | [%] | [date] | [Yes/No/Partial] | [any discrepancy or detail] |

Fill in EVERY row for all projects. The "Verified" column should be:
- **Yes** — the noted action matches what's actually in Linear
- **No** — the noted action does NOT match reality
- **Partial** — some aspects match, others don't
- **N/A** — project not found in Linear (may have been deleted)

After the table, add a section:

## Action Items

List any projects where the noted action has NOT been carried out yet. For example:
- If "deleted" was noted but the project still exists
- If "merged" was noted but both projects still exist separately
- If "done" was noted but the state is not "completed"
- If "assigned to X" was noted but no assignee is set

Format as:
- **[Project Name]**: [what was noted] → [what actually is] — [recommended action]

## Fully Verified

List projects where everything checks out (noted action matches reality).

IMPORTANT: Do NOT skip any project. All must appear in the table."""


def _extract_connect_url(err: AuthenticationError) -> str | None:
    body = err.body if isinstance(err.body, dict) else {}
    return body.get("connect_url") or body.get("detail", {}).get("connect_url")


def _prompt_oauth(url: str) -> None:
    print("\nLinear OAuth required. Opening browser...")
    print(f"   If browser doesn't open, visit:\n   {url}")
    webbrowser.open(url)
    input("\n   Press Enter after completing OAuth...")


async def main() -> None:
    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See env.example.")
        sys.exit(1)
    if not LINEAR_MCP_SERVER:
        print("Error: LINEAR_MCP_SERVER not set. See env.example.")
        sys.exit(1)

    report_path = REPORT_PATH
    if not os.path.exists(report_path):
        print(f"Error: Report not found at {report_path}")
        sys.exit(1)

    with open(report_path) as f:
        report_text = f.read()

    projects = parse_projects(report_text)
    print(f"Parsed {len(projects)} projects from report\n")
    for p in projects:
        tag = f" ({p['action']})" if p['action'] else ""
        print(f"  {p['rank']:>2}. {p['title']}{tag}")

    print(f"\n{'=' * 60}")
    print(f"  Verifying against Linear via {LINEAR_MCP_SERVER}")
    print(f"  Model: {MODEL}")
    print(f"{'=' * 60}\n")

    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)
    prompt = build_prompt(projects)

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
        result = await execute()
    except AuthenticationError as err:
        url = _extract_connect_url(err)
        if not url:
            raise
        _prompt_oauth(url)
        result = await execute()

    print()

    if result.strip():
        with open("linear_revamp_projects.md", "w") as f:
            f.write(result)
        print(f"\nReport saved to: linear_revamp_projects.md")
    else:
        print("No output generated.")


if __name__ == "__main__":
    asyncio.run(main())
