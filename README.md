# Linear Helper

A set of AI-powered scripts that analyze your [Linear](https://linear.app) workspace for neglected projects and issues, then help you triage and clean them up — all through the [Dedalus SDK](https://docs.dedaluslabs.ai) and a hosted Linear MCP server.

## What It Does

1. **Compile a neglect report** — Fetches every project and issue for a team, scores them by staleness, and outputs a ranked markdown report.
2. **Verify project actions** — After you annotate the report with planned actions (rename, merge, delete, etc.), cross-checks each action against the live Linear state.
3. **Categorize and apply issue changes** — Reads annotated issue IDs from the report, categorizes them, and batch-applies state changes on Linear (cancel, mark done, move to a project).

## Architecture

```
┌──────────────────────┐
│  compile_neglected_  │  Step 1: Generate neglect report
│  report.py           │──────► neglect_report_YYYY-MM-DD.md
└──────────────────────┘
         │
         ▼  (you annotate the report)
┌──────────────────────┐
│  project_revamp_     │  Step 2: Verify project-level actions
│  check.py            │──────► linear_revamp_projects.md
└──────────────────────┘
         │
┌──────────────────────┐
│  categorize_         │  Step 3: Categorize & apply issue changes
│  issues.py           │──────► linear_revamp_loose_issues.md
└──────────────────────┘
         │
         ▼
   Linear workspace updated
```

Each script uses the Dedalus SDK to dispatch an LLM agent that calls a hosted Linear MCP server. Your Linear API key is encrypted client-side via Dedalus Auth and only decrypted inside the secure enclave at runtime.

## Prerequisites

- Python 3.11+
- A [Dedalus](https://dedalus.dev) account and API key
- A [Linear](https://linear.app) personal API key
- A hosted Linear MCP server on the Dedalus marketplace (e.g. one built with [linear-mcp](https://github.com/NickyHeC/linear-mcp))

## Setup

```bash
# Clone and enter the project
git clone <repo-url> && cd linear-helper

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp env.example .env
# Edit .env with your actual keys and server slug
```

### Required Environment Variables

| Variable | Description |
|---|---|
| `DEDALUS_API_KEY` | Your Dedalus platform API key |
| `LINEAR_API_KEY` | Linear personal API key (Settings > Account > API) |
| `LINEAR_MCP_SERVER` | Slug of the Linear MCP server on Dedalus (e.g. `username/linear-mcp`) |

### Optional Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MODEL` | `anthropic/claude-sonnet-4-20250514` | LLM model for analysis |
| `AGENT_TIMEOUT` | `600` | Agent timeout in seconds |
| `LINEAR_TEAM_NAME` | `Engineering` | Default team to analyze |
| `REPORT_PATH` | `neglect_report.md` | Path to a previously generated report |
| `ACTION_NEEDED_IDS` | *(empty)* | Comma-separated issue IDs needing manual action |
| `MOVE_TO_PROJECT_IDS` | *(empty)* | Comma-separated issue IDs to move to a project |
| `MOVE_TO_PROJECT_NAME` | *(empty)* | Target project name for moved issues |

## Usage

### Step 1: Generate the Neglect Report

```bash
# Interactive — prompts for team name
python compile_neglected_report.py

# Specify team and save to file
python compile_neglected_report.py "Engineering" --save
```

This produces `neglect_report_YYYY-MM-DD.md` containing:
- A summary table of total projects, issues, and the most neglected items
- Every project ranked by neglect score, with its issues nested underneath
- A "Loose Issues" section at the end for issues not belonging to any project

**Neglect scoring:**
- `days_stale` = days since last update
- State multiplier: Triage/Backlog = 2.0, Todo = 1.5, In Progress = 1.0, Done/Cancelled = 0.0
- Unassigned bonus: 1.3x if no assignee
- `neglect_score = days_stale × state_multiplier × assignee_bonus`

### Step 2: Annotate the Report

Open the generated report and annotate project headings with actions in parentheses:

```markdown
### 1. Some Neglected Project (delete)
### 2. Old Feature (merge with New Feature)
### 3. Active Work (in progress)
```

For loose issues, edit the ID column to mark actions:

| Annotation | Meaning |
|---|---|
| `ID x` | Cancel this issue |
| `++ID++` | Mark as done |
| `ID (stale)` | Stale — no action needed |
| `ID (check)` | Needs review — no action |

### Step 3: Verify Project Actions

```bash
REPORT_PATH=neglect_report_2026-03-08.md python project_revamp_check.py
```

The agent reads your annotated project actions, queries Linear for the actual current state of each project, and writes a verification report to `linear_revamp_projects.md` showing which actions match reality and which still need to be carried out.

### Step 4: Apply Issue Changes

```bash
# Basic — just categorize by annotations
REPORT_PATH=neglect_report_2026-03-08.md python categorize_issues.py

# With extra config — move specific issues to a project
ACTION_NEEDED_IDS=TEAM-1,TEAM-2 \
MOVE_TO_PROJECT_IDS=TEAM-3,TEAM-4 \
MOVE_TO_PROJECT_NAME="Docs Revamp" \
REPORT_PATH=neglect_report_2026-03-08.md \
python categorize_issues.py
```

This will:
- Parse the Loose Issues table from the report
- Categorize each issue (currently open, check, stale, done, action needed, cancelled)
- Apply changes on Linear: cancel `x` issues, mark `++` issues as done, move specified issues to a project
- Write `linear_revamp_loose_issues.md` with all issues sorted into categories with links

### OAuth Flow

On the first run, the Dedalus platform may require a one-time OAuth authorization with Linear. The script will print a URL — open it in your browser, authorize, then return to the terminal and press Enter.

## Project Structure

```
linear-helper/
├── compile_neglected_report.py   # Step 1: Generate neglect report
├── project_revamp_check.py       # Step 2: Verify project actions
├── categorize_issues.py          # Step 3: Categorize & apply issue changes
├── connection.py                 # Linear MCP credential configuration
├── requirements.txt              # Python dependencies
├── env.example                   # Environment variable template
└── .gitignore
```

## License

MIT
