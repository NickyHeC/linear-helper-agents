"""Linear Neglect Analyzer — finds neglected projects and issues in Linear.

Uses the Dedalus SDK to connect to a hosted Linear MCP server, gathers all
projects and issues for a given team, scores them by neglect, and outputs
a ranked report.

Copy this file to compile_neglected_report.py.

Usage:
    python compile_neglected_report.py                    # interactive
    python compile_neglected_report.py "My Team"          # specify team name
    python compile_neglected_report.py "My Team" --save   # save report to file
"""

import asyncio
import os
import sys
import webbrowser
from datetime import datetime

from dedalus_labs import AsyncDedalus, AuthenticationError, DedalusRunner
from dotenv import load_dotenv


load_dotenv()

from connection import linear_secrets

LINEAR_MCP_SERVER = os.getenv("LINEAR_MCP_SERVER")
MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "600"))


def build_prompt(team_name: str, today: str) -> str:
    """Build the analysis prompt for the agent."""
    return f"""You are a Linear workspace analyst. Analyze the "{team_name}" team's projects and issues to produce a neglect report.

Today's date: {today}

## Instructions — follow every step precisely

### Step 1: Find the team
Call the `linear_list_teams` tool. Identify the team whose name matches or contains "{team_name}". If no exact match, pick the closest. Record its team ID and key.

### Step 2: Fetch ALL projects for this team
Call `linear_list_projects` with the team filter set to the team name/ID. If there are more results (check pagination cursor), fetch subsequent pages until all projects are retrieved.

For each project record: id, name, state, progress, targetDate, createdAt, updatedAt.

### Step 3: Fetch ALL issues for this team
Call `linear_list_issues` with the team filter. **You MUST paginate**: if the response includes a cursor for the next page, call again with that cursor. Repeat until no more pages remain.

For each issue record: id, identifier, title, state, priority, assignee, project (if any), createdAt, updatedAt.

### Step 4: Compute neglect scores
For each issue, calculate:
- `days_stale` = number of days between its updatedAt and today ({today})
- `state_multiplier`:
  - Triage / Backlog = 2.0
  - Unstarted / Todo = 1.5
  - In Progress / Started / In Review = 1.0
  - Done / Completed / Cancelled / Closed / Merged / Duplicate = 0.0  (these are NOT neglected)
- `assignee_bonus` = 1.3 if no assignee, else 1.0
- `neglect_score` = days_stale × state_multiplier × assignee_bonus

For each project:
- `project_days_stale` = days between project's updatedAt and today
- `issue_avg_neglect` = average neglect_score of all issues in this project (0 if no issues)
- `project_neglect_score` = (project_days_stale × 0.3) + (issue_avg_neglect × 0.7)
- If project state is "completed" or "cancelled": project_neglect_score = 0

### Step 5: Output the report

Output EXACTLY this format (markdown):

---

# Linear Neglect Analysis — {team_name} Team
**Generated:** {today}

## Summary
| Metric | Value |
|--------|-------|
| Total Projects | [N] |
| Total Issues | [N] |
| Issues with No Project | [N] |
| Most Neglected Project | [name] (score: [X]) |
| Most Neglected Issue | [identifier]: [title] (score: [X]) |

## Projects — Most Neglected First

For each project (sorted by project_neglect_score descending):

### [rank]. [Project Name]
| Attribute | Value |
|-----------|-------|
| State | [state] |
| Progress | [progress%] |
| Created | [YYYY-MM-DD] |
| Last Updated | [YYYY-MM-DD] |
| Days Since Update | [N] |
| Neglect Score | [X] |

**Issues** (sorted by neglect_score descending within this project):

| # | ID | Title | State | Priority | Assignee | Created | Updated | Days Stale | Neglect Score |
|---|----|-------|-------|----------|----------|---------|---------|------------|---------------|
| 1 | ENG-123 | Title here | In Progress | High | Alice | 2025-01-15 | 2025-06-01 | 280 | 280.0 |

If a project has no issues, write: *No issues in this project.*

### Loose Issues (No Project)

This section MUST appear LAST, after all projects. List every issue that has no project assigned:

| # | ID | Title | State | Priority | Assignee | Created | Updated | Days Stale | Neglect Score |
|---|----|-------|-------|----------|----------|---------|---------|------------|---------------|

If no loose issues exist, write: *All issues belong to a project.*

---

## Critical rules
- Include EVERY project and EVERY issue. None may be omitted.
- Use pagination to fetch all results. Do NOT stop at the first page.
- Dates must be formatted as YYYY-MM-DD.
- Neglect scores should be rounded to 1 decimal place.
- Projects with score 0 (completed/cancelled) still appear in the list, just at the bottom.
- Loose issues section is ALWAYS last, regardless of scores.
- Priority labels: 0=None, 1=Urgent, 2=High, 3=Normal, 4=Low. Display the label, not the number.
- Do NOT add any commentary outside the report format. Output ONLY the report."""


def _extract_connect_url(err: AuthenticationError) -> str | None:
    body = err.body if isinstance(err.body, dict) else {}
    return body.get("connect_url") or body.get("detail", {}).get("connect_url")


def _prompt_oauth(url: str) -> None:
    print("\nLinear OAuth required. Opening browser...")
    print(f"   If browser doesn't open, visit:\n   {url}")
    webbrowser.open(url)
    input("\n   Press Enter after completing OAuth...")


async def run_analysis(team_name: str, save_to_file: bool = False) -> str:
    """Run the neglect analysis agent and return the report."""
    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = build_prompt(team_name, today)

    print(f"Linear Neglect Analyzer")
    print(f"{'=' * 50}")
    print(f"  Team:  {team_name}")
    print(f"  Date:  {today}")
    print(f"  Model: {MODEL}")
    print(f"  MCP:   {LINEAR_MCP_SERVER}")
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

    if save_to_file and report.strip():
        filename = f"neglect_report_{today}.md"
        with open(filename, "w") as f:
            f.write(report)
        print(f"\nReport saved to: {filename}")

    return report


async def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] not in ("--help", "-h"):
        team_name = sys.argv[1]
    else:
        if "--help" in sys.argv or "-h" in sys.argv:
            print(__doc__)
            return
        default_team = os.getenv("LINEAR_TEAM_NAME", "Engineering")
        team_name = (
            input(f"Team name [{default_team}]: ").strip() or default_team
        )

    save = "--save" in sys.argv

    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See env.example.")
        sys.exit(1)
    if not LINEAR_MCP_SERVER:
        print("Error: LINEAR_MCP_SERVER not set. See env.example.")
        sys.exit(1)

    await run_analysis(team_name, save_to_file=save)


if __name__ == "__main__":
    asyncio.run(main())
