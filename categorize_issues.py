"""Categorize loose issues and apply Linear changes.

Parses the neglect report's Loose Issues section, categorizes each issue
by its ID-column annotations, applies changes on Linear (cancel, done, move),
and writes a categorized report to linear_revamp_loose_issues.md.

ID column annotation conventions:
  - "ID x"      → cancel the issue
  - "++ID++"    → mark as done
  - "ID (stale)"→ stale category (no Linear action)
  - "ID (check)"→ check category (no Linear action)
  - No edits    → currently open

Set ACTION_NEEDED_IDS, MOVE_TO_PROJECT_IDS, and MOVE_TO_PROJECT_NAME via
environment variables (comma-separated for lists) to customize which issues
get special treatment.

Usage:
    python categorize_issues.py
    REPORT_PATH=neglect_report.md python categorize_issues.py
"""

import asyncio
import os
import re
import sys
import webbrowser
from datetime import datetime

from dedalus_labs import AsyncDedalus, AuthenticationError, DedalusRunner
from dotenv import load_dotenv


load_dotenv()

from connection import linear_secrets

LINEAR_MCP_SERVER = os.getenv("LINEAR_MCP_SERVER")
MODEL = os.getenv("MODEL", "anthropic/claude-sonnet-4-20250514")
TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "900"))
REPORT_PATH = os.getenv("REPORT_PATH", "neglect_report.md")

_action_raw = os.getenv("ACTION_NEEDED_IDS", "")
ACTION_NEEDED_IDS = {s.strip() for s in _action_raw.split(",") if s.strip()}

_move_raw = os.getenv("MOVE_TO_PROJECT_IDS", "")
MOVE_TO_PROJECT_IDS = {s.strip() for s in _move_raw.split(",") if s.strip()}

MOVE_TO_PROJECT_NAME = os.getenv("MOVE_TO_PROJECT_NAME", "")


def parse_loose_issues(report_path: str) -> list[dict]:
    with open(report_path) as f:
        content = f.read()

    marker = "### Loose Issues (No Project)"
    start = content.index(marker)
    section = content[start:]

    rows = []
    in_table = False
    for line in section.split("\n"):
        if line.startswith("| #"):
            in_table = True
            continue
        if line.startswith("| ---"):
            continue
        if in_table and line.startswith("|"):
            cols = [c.strip() for c in line.split("|")]
            cols = cols[1:-1]
            if len(cols) >= 10:
                rows.append({
                    "num": cols[0].strip(),
                    "id_raw": cols[1].strip(),
                    "title": cols[2].strip(),
                    "state": cols[3].strip(),
                    "priority": cols[4].strip(),
                    "assignee": cols[5].strip(),
                    "created": cols[6].strip(),
                    "updated": cols[7].strip(),
                    "days_stale": cols[8].strip(),
                    "neglect_score": cols[9].strip(),
                })
    return rows


def extract_identifier(id_raw: str) -> str:
    cleaned = id_raw.replace("++", "").strip()
    cleaned = re.sub(r"\s*x\s*$", "", cleaned)
    cleaned = re.sub(r"\s*\(.*\)\s*$", "", cleaned)
    return cleaned.strip()


def categorize(row: dict) -> str:
    id_raw = row["id_raw"]
    identifier = extract_identifier(id_raw)

    if identifier in ACTION_NEEDED_IDS:
        return "action_needed"
    if identifier in MOVE_TO_PROJECT_IDS:
        return "move_to_project"
    if id_raw.rstrip().endswith("x"):
        clean_check = re.sub(r"\s*\(.*\)", "", id_raw).strip()
        if clean_check.endswith("x"):
            return "cancelled"
    if id_raw.startswith("++") and "++" in id_raw[2:]:
        return "done"
    if "(stale" in id_raw.lower():
        return "stale"
    if "(check" in id_raw.lower():
        return "check"
    return "currently_open"


def build_agent_prompt(cancelled, done, move_to_project):
    parts = []
    parts.append("You are a Linear workspace manager. Perform the following changes on Linear issues:\n")

    if cancelled:
        cancel_list = "\n".join(f"  - {r['identifier']}" for r in cancelled)
        parts.append(f"""## 1. Cancel these issues (set state to Cancelled)
{cancel_list}

For each issue above, call `linear_update_issue` to change its state to cancelled. You'll need to find the Cancelled state ID for each issue's team first using `linear_list_team_states`.
""")

    if done:
        done_list = "\n".join(f"  - {r['identifier']}" for r in done)
        parts.append(f"""## 2. Mark these issues as Done (set state to Done)
{done_list}

For each issue above, call `linear_update_issue` to change its state to done/completed. You'll need the Done state ID for each team.
""")

    if move_to_project and MOVE_TO_PROJECT_NAME:
        move_list = "\n".join(f"  - {r['identifier']}" for r in move_to_project)
        parts.append(f"""## 3. Move these issues to the "{MOVE_TO_PROJECT_NAME}" project
{move_list}

For each issue above:
- Find the "{MOVE_TO_PROJECT_NAME}" project using `linear_list_projects`
- Call `linear_update_issue` to set the project_id to that project
- If the issue is on a different team than the project, update the team_id as well
""")

    parts.append("""## Final step: Get the workspace URL slug
Call `linear_whoami` or check any issue URL to determine the workspace slug (e.g., "myorg" from linear.app/myorg/...).

## Output
After completing ALL changes, output ONLY a JSON object with this exact structure (no markdown, no explanation):
{{"workspace_slug": "the-slug", "results": [{{"identifier": "XXX-123", "action": "cancelled|done|moved", "success": true|false, "note": "any error or detail"}}]}}
""")
    return "\n".join(parts)


def _extract_connect_url(err):
    body = err.body if isinstance(err.body, dict) else {}
    return body.get("connect_url") or body.get("detail", {}).get("connect_url")


def _prompt_oauth(url):
    print("\nLinear OAuth required. Opening browser...")
    print(f"   URL: {url}")
    webbrowser.open(url)
    input("\n   Press Enter after completing OAuth...")


def generate_output(categories, workspace_slug):
    base = f"https://linear.app/{workspace_slug}/issue"

    lines = [
        "# Linear Loose Issues — Categorized Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}",
        f"**Workspace:** [{workspace_slug}](https://linear.app/{workspace_slug})",
        "",
    ]

    order = [
        ("currently_open", "Currently Open"),
        ("check", "Check"),
        ("stale", "Stale"),
        ("done", "Done"),
        ("action_needed", "Action Needed"),
        ("cancelled", "Cancelled"),
    ]

    for cat_key, cat_title in order:
        items = categories.get(cat_key, [])
        lines.append(f"## {cat_title} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("*No issues in this category.*")
            lines.append("")
            continue

        lines.append("| # | ID | Title | State | Priority | Assignee | Link |")
        lines.append("|---|-----|-------|-------|----------|----------|------|")
        for i, item in enumerate(items, 1):
            ident = item["identifier"]
            link = f"[{ident}]({base}/{ident})"
            lines.append(
                f"| {i} | {ident} | {item['title']} | {item['state']} "
                f"| {item['priority']} | {item['assignee']} | {link} |"
            )
        lines.append("")

    move_items = categories.get("move_to_project", [])
    if move_items:
        project_label = MOVE_TO_PROJECT_NAME or "Target Project"
        lines.append(f"## Moved to {project_label} ({len(move_items)})")
        lines.append("")
        lines.append("| # | ID | Title | State | Priority | Assignee | Link |")
        lines.append("|---|-----|-------|-------|----------|----------|------|")
        for i, item in enumerate(move_items, 1):
            ident = item["identifier"]
            link = f"[{ident}]({base}/{ident})"
            lines.append(
                f"| {i} | {ident} | {item['title']} | {item['state']} "
                f"| {item['priority']} | {item['assignee']} | {link} |"
            )
        lines.append("")

    return "\n".join(lines)


async def main():
    if not os.getenv("DEDALUS_API_KEY"):
        print("Error: DEDALUS_API_KEY not set. See env.example.")
        sys.exit(1)
    if not LINEAR_MCP_SERVER:
        print("Error: LINEAR_MCP_SERVER not set. See env.example.")
        sys.exit(1)

    print("Parsing loose issues from report...")
    rows = parse_loose_issues(REPORT_PATH)
    print(f"Found {len(rows)} loose issues\n")

    categories = {}
    enriched_rows = []
    for row in rows:
        cat = categorize(row)
        identifier = extract_identifier(row["id_raw"])
        enriched = {**row, "identifier": identifier, "category": cat}
        enriched_rows.append(enriched)
        categories.setdefault(cat, []).append(enriched)

    for cat_key in ["currently_open", "check", "stale", "done", "action_needed", "cancelled", "move_to_project"]:
        items = categories.get(cat_key, [])
        label = cat_key.replace("_", " ").title()
        print(f"  {label}: {len(items)}")
    print()

    cancelled = categories.get("cancelled", [])
    done = categories.get("done", [])
    move_to_project = categories.get("move_to_project", [])

    print(f"Linear changes to make:")
    print(f"  Cancel:         {len(cancelled)} issues")
    print(f"  Mark done:      {len(done)} issues")
    print(f"  Move to project: {len(move_to_project)} issues")
    print(f"{'=' * 60}\n")

    client = AsyncDedalus(timeout=TIMEOUT)
    runner = DedalusRunner(client)
    prompt = build_agent_prompt(cancelled, done, move_to_project)

    async def execute():
        response = runner.run(
            input=prompt,
            model=MODEL,
            mcp_servers=[LINEAR_MCP_SERVER],
            credentials=[linear_secrets.to_dict()],
            stream=True,
            max_steps=30,
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

    print("\n")

    workspace_slug = "workspace"
    try:
        import json
        clean = result.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```\w*\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
        data = json.loads(clean)
        workspace_slug = data.get("workspace_slug", "workspace")
        print(f"Workspace slug: {workspace_slug}")
        results = data.get("results", [])
        success_count = sum(1 for r in results if r.get("success"))
        print(f"Linear changes: {success_count}/{len(results)} successful")
    except (json.JSONDecodeError, Exception) as e:
        print(f"Could not parse agent JSON output ({e}), using default workspace slug")

    report = generate_output(categories, workspace_slug)

    with open("linear_revamp_loose_issues.md", "w") as f:
        f.write(report)
    print(f"\nSaved to: linear_revamp_loose_issues.md")


if __name__ == "__main__":
    asyncio.run(main())
