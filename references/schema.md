# Data schema

The canonical format is UTF-8 JSON with `schema_version: 1`.

## Dataset

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-27T20:00:00+08:00",
  "coverage": {
    "expected": 33,
    "verified": 1,
    "pending": 32,
    "not_found": 0,
    "checked_at": "2026-08-27T20:00:00+08:00"
  },
  "exams": []
}
```

The required jurisdiction baseline is national, 31 mainland provincial-level regions, and the Xinjiang Production and Construction Corps: 33 entries. Municipal and selected-graduate series add to this baseline rather than changing it.

`coverage.expected` must equal the unique region count in the official-source registry. `verified + pending + not_found` must equal `expected`, and `checked_at` must be an ISO datetime with a timezone. National and provincial exam regions must match the declared authority's registry region. `verified` cannot exceed the number of baseline jurisdictions containing at least one human-approved event, so an all-candidate dataset must report zero verified jurisdictions.

`coverage` remains the 33-jurisdiction compatibility baseline. Use `coverage_scopes` for accurate year/type filtering:

```json
{
  "coverage_scopes": [
    {
      "recruitment_year": 2027,
      "exam_type": "selected_graduate",
      "scope_mode": "partial",
      "expected": 1,
      "verified": 0,
      "candidate": 0,
      "pending": 1,
      "not_found": 0,
      "checked_at": "2026-09-12T12:00:00+08:00",
      "regions": [
        {"region_code": "110000", "region_name": "北京", "status": "pending"}
      ]
    }
  ]
}
```

The example is a one-region refresh. Set `scope_mode` to `partial` for a regional work file and `full` for a complete scope; omitted mode is treated as `full` for compatibility. Each scope key `(recruitment_year, exam_type)` is unique. `expected` equals the length of `regions`; `verified + candidate + pending + not_found` equals `expected`; the counters must equal the region-state counts. A `verified` region requires at least one matching approved event, while a `candidate` region requires at least one matching candidate event. A full nationwide `selected_graduate` scope must contain exactly the 31 mainland provincial-level region codes and excludes `CN` and the Xinjiang Production and Construction Corps code `660000`. A partial scope can only be promoted by merging it into an existing full scope, so a single-region refresh cannot shrink national coverage.

## Exam

Required fields:

- `id`: stable lowercase ASCII identifier, such as `bj-provincial-2026`.
- `recruitment_year`: integer batch year.
- `title`: official recruitment title.
- `exam_type`: `national`, `provincial`, `municipal`, or `selected_graduate`.
- `region_code`: `CN` or GB/T 2260-style provincial code used by the registry.
- `region_name`: Chinese display name.
- `events`: array of event objects.

## Event

Required fields:

- `id`: stable across later official corrections.
- `stage`: `announcement`, `registration`, `qualification`, `payment`, `admit_card`, `written_exam`, or `score`.
- `label`: concise Chinese event label.
- `start`, `end`: ISO `YYYY-MM-DD`, `YYYY-MM`, or ISO datetime values matching `precision`.
- `precision`: `date`, `month`, or `datetime`.
- `status`: `confirmed`, `tentative`, or `changed`.
- `source`: evidence object.
- `review`: human decision object.

Use `tentative` only when the official source says 预计、初定、拟于, or equivalent. Use `changed` when a later official correction supersedes an already published event.

## Source evidence

Required fields:

- `authority_id`: registry ID whose host whitelist must contain the source URL.
- `title`: official page title.
- `url`: exact official notice URL; registry `entry_urls` and obvious home/index/search/list pages are rejected automatically, and the reviewer must manually confirm all other URLs.
- `publisher`: named issuing authority.
- `published_at`: official page publication date.
- `retrieved_at`: ISO datetime with timezone.
- `evidence`: date-bearing source excerpt that directly supports the event date, at most 240 characters.

## Review

Candidate data uses `status: candidate`. Promotion requires:

```json
{
  "status": "approved",
  "reviewed_at": "2026-08-27T20:10:00+08:00",
  "reviewed_by": "maintainer",
  "source_url_verified": true
}
```

`source_url_verified` records the reviewer's explicit confirmation that the URL is the exact supporting notice rather than a home, topic, search, or section-list page. Rejected or still-unreviewed events remain outside the published layer.
