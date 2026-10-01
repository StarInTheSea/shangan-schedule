---
name: shangan-schedule
description: Build or update a Chinese civil-service recruitment timetable from official national, provincial, independent municipal, and selected-graduate notices. Use when collecting, reviewing, validating, or rendering 公务员考试、国考、省考、市考、选调生 dates; do not use for training advice, position matching, or unofficial date predictions.
metadata:
  short-description: Build a verified Chinese civil-service exam schedule
---

# Shangan Schedule

Create a source-backed recruitment-year timetable and a static Chinese website. Treat collected facts as candidates until the user explicitly approves them.

## Requirements and paths

Requires Python 3.10+ and an Agent with web browsing and shell/file access. The Python helpers use only the standard library; no API keys or service accounts are required by this Skill. The host Agent may have its own account requirements. If browsing is unavailable, only validate or render existing data and report collection as blocked.

Resolve `SKILL_ROOT` to the directory containing this `SKILL.md`. Run helper scripts by absolute path (for example, `python3 "$SKILL_ROOT/scripts/shangan_schedule.py" ...`) from the user's working directory. The CLI defaults to its bundled source registry and site assets. Keep output paths in the user's working directory, outside the installed Skill; do not `cd` into the installation to write collected data. Commands below are repository-development examples: when installed, replace `scripts/...` with the absolute helper path and omit `--sources references/official-sources.json` and `--assets assets/site` to use the bundled defaults. Resolve reference links relative to `SKILL_ROOT` too.

## Start

Read the requested recruitment year, exam type, and optional region from the user's prompt. Recruitment year is the batch label, so a 2027 batch may contain events in 2026. Default generated artifacts to `.shangan-schedule/<year>/`; keep this path out of version control unless the user explicitly requests publication. This workflow is Agent-platform-neutral: use the host Agent's browser and shell capabilities without assuming Codex-specific commands.

Read:

- [references/collection-workflow.md](references/collection-workflow.md) before discovering or changing data.
- [references/schema.md](references/schema.md) before authoring candidate JSON.
- [references/official-sources.json](references/official-sources.json) when accepting or rejecting a source domain.

For a new scope, initialize the coverage matrix without overwriting existing work:

```bash
python3 scripts/shangan_schedule.py init \
  --year 2027 \
  --type selected_graduate \
  --sources references/official-sources.json \
  --output .shangan-schedule/2027/candidates.json
```

Add `--region <region_code>` only when the user requests a single-region refresh. It creates a `scope_mode: partial` work file; promotion must merge it into an existing full scope and will refuse to publish it alone.

## Required boundary

Cover announcement, registration, qualification review, payment confirmation, admission-ticket printing, written exams, and score lookup. Exclude interviews, physical examinations, inspections, hiring lists, public selection for serving civil servants, public institutions, military civilian roles, and other non-civil-service recruitment.

Classify independent city exams as `municipal` only when the city runs a separate recruitment with its own official notice. City and county positions inside a provincial joint exam remain `provincial`.

## Collect

Use available web browsing to discover notices. Prefer the original authority; an official government repost is only a fallback. Third-party sites may supply search clues but never a formal event URL or evidence source.

For each event:

1. Open the official notice and verify the title, publisher, publication date, event date or range, precision, and whether it is tentative.
2. Save a date-bearing evidence excerpt of at most 240 characters. Do not infer a day from unofficial forecasts.
3. Record the exact notice URL, not an agency home page, topic page, or registry `entry_urls` value.
4. Mark the review status `candidate`.

Use `scripts/snapshot_official.py` for a rate-limited local snapshot only when direct fetching is permitted. It treats successful HTTP responses that look like login, CAPTCHA, or access-gate pages as blocked. Stop on those pages, robots denial, access blocks, or unexpected large downloads; do not bypass controls.

## Manual refresh

When the user asks to pull the latest status:

1. Open the matching authorities and search their official hosts. Search both the recruitment year and the preceding calendar year.
2. Preserve existing stable exam and event IDs. Add or revise facts only when an exact whitelisted official notice supports them.
3. Update the matching `coverage_scopes` region state: `candidate` for a newly extracted notice, `pending` when the search is incomplete or blocked, and `not_found` only after a documented official-site check.
4. Keep every new or changed event at `review.status: candidate` and validate the complete file.
5. Generate the deterministic review report shown below.

## Validate and review

Run:

```bash
python3 scripts/shangan_schedule.py validate \
  --input <candidate.json> \
  --sources references/official-sources.json

python3 scripts/shangan_schedule.py review \
  --candidate <candidate.json> \
  --published <published.json> \
  --sources references/official-sources.json \
  --output <review.md>
```

Present new events, changed dates, source changes, proposed whitelist changes, link-access failures, and coverage gaps to the user. Deterministic review does not test live page reachability. Ask for approval before changing any `review.status` to `approved`. For every approved event, open the URL, verify that it is the exact supporting notice, record `review.source_url_verified: true`, and change the matching scope region from `candidate` to `verified`; URL-shape checks alone are insufficient. Do not promote, render, deploy, or publish while approval is missing.

After approval, run:

```bash
python3 scripts/shangan_schedule.py promote \
  --input <candidate.json> \
  --sources references/official-sources.json \
  --output <published.json> \
  --history <history-directory> \
  --review-id <unique-review-id>
```

Never reuse a review ID. Promotion reserves the receipt with `status: pending` before it writes a prior snapshot or published data, then finalizes it as `completed`. If a run is interrupted, retain the pending receipt and prior snapshot for recovery, inspect the recorded hashes and output path, and use a new review ID for any later promotion. A changed event keeps its stable event ID so the receipt can report the change.

## Render

Only render `published.json`:

```bash
python3 scripts/shangan_schedule.py build \
  --data <published.json> \
  --sources references/official-sources.json \
  --assets assets/site \
  --output <site-directory>
```

The site is a generated artifact. Month-precision events appear on the first day of the applicable month in the calendar so they do not disappear. The static page does not update itself; its copy button creates a prompt that can be pasted into any compatible Agent to rerun this workflow. Do not deploy it or commit real collected data unless the user separately authorizes that action.

## Completion check

Report the recruitment year, verified/candidate/pending/not-found scoped coverage, validation result, generated paths, and unresolved official-source gaps. Never call the timetable nationally complete when any required jurisdiction remains pending or not found.
