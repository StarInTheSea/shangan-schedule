# Collection workflow

## 1. Establish the batch

Treat the requested year as a recruitment year, not a calendar-year filter. Search the preceding calendar year as well as the named year.

Run `scripts/shangan_schedule.py init` when the requested year/type scope does not exist. It creates a non-overwriting `coverage_scopes` matrix. Create coverage for:

- National civil-service recruitment.
- 31 mainland provincial-level regions.
- Xinjiang Production and Construction Corps as a separate region.
- Independent municipal recruitment found outside provincial joint exams.
- Public ordinary or targeted selected-graduate recruitment for new graduates.

Use `verified`, `candidate`, `pending`, and `not_found` precisely. `candidate` means an exact official notice was extracted but has not received user approval. `not_found` means a documented official-site check found no public notice; it does not assert that no recruitment exists. A nationwide selected-graduate scope covers the 31 mainland provincial-level regions; it excludes the national exam and Xinjiang Production and Construction Corps baselines.

Single-region initialization writes `scope_mode: partial`. It is a work-file boundary, not a publishable 1/1 national scope. Promotion merges that region into an existing `scope_mode: full` matrix and refuses a standalone partial scope.

## 2. Discover official notices

Search the authority and official exam portal named in `official-sources.json`. Prefer, in order:

1. Original civil-service authority or organization-department notice.
2. Original official personnel-exam portal.
3. An official government-portal repost that names the original issuer.

Do not accept a page merely because its host ends in `.gov.cn`; the host must appear under the matching authority in the registry. When an official authority moves domains, show the proposed registry change and evidence to the user before accepting it.

## 3. Extract events

Collect only the stages in the schema. Registration, qualification, payment, admission-ticket, and written-exam ranges often share one announcement URL. A score event should link to the later official score notice when available; an announcement's official “预计某月” statement remains `tentative` until replaced.

One provincial joint exam remains one exam series even when city agencies publish position-specific pages. For selected-graduate recruitment, deduplicate university reposts that refer to the same provincial batch.

## 4. Record evidence

Each event needs an exact URL and a short date-bearing excerpt. Obvious home, index, search, and section-list pages are rejected automatically; a human reviewer must still open every surviving URL and confirm that it is the exact notice rather than relying on URL shape alone. Record retrieval time in Asia/Shanghai. If page content is dynamic or an attachment cannot be read, keep the jurisdiction pending and explain the blocker.

Do not bypass CAPTCHA, authentication, robots restrictions, rate limits, or access controls. Never send personal application data to a source site.

## 5. Review and promotion

Validate candidate JSON before showing it. The review summary should group:

- new exams and events;
- changes to already published event IDs;
- duplicate or conflicting dates;
- official domains proposed for whitelist addition;
- missing stages and jurisdictions.

Run the deterministic `review` command and show its new/changed/source-change/coverage-gap sections. Deterministic review does not test live page reachability, so the Agent and reviewer must still open every exact link. Wait for explicit approval. Mark only approved events and record `source_url_verified: true` after the reviewer opens and confirms each exact supporting notice. Change the matching scope region from `candidate` to `verified`, then update its counters. Promote with a unique review ID, and keep the receipt plus prior published snapshot. The receipt is written as `pending` before any published-data mutation and finalized as `completed`; an interrupted pending batch remains consumed and must not be deleted or reused.

## 6. Render and report

Build from the published layer only. The static page may contain pending coverage scopes but never candidate events. Check it at desktop and mobile widths, verify external official links, and report the selected year/type coverage ratio. The page cannot update itself: its copy button provides a platform-neutral prompt for rerunning this workflow in a compatible Agent. Deployment and public publication are separate user-authorized actions.
