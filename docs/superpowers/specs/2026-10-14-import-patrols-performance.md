# import-patrols: where the time goes, and how to make it faster

Written 2026-10-14 during the first full-CA import (Lawin Region 05 - Albay,
`050500`, into phinney.staging.pamdas.org). Companion to
`2026-10-12-smart-patrol-migration-design.md`. Numbers below are measured on
that run unless marked *estimate*.

## The problem

Albay is a mid-sized CA: 1,527 patrols, 14,061 observations, 585 attachments,
285,181 track points. The import runs at **~2.6 patrols a minute (≈23 s per
patrol)**, so the whole CA takes **about 9–10 hours**. Guinobatan (`050504`,
1,038 patrols, 17,468 observations, 391k points) would be similar. A region
has several CAs; a national rollout has dozens. Hours per CA is workable for
a pilot and not for a programme.

Nothing is failing or retrying. The run is slow because it is **strictly
sequential and chatty**: one HTTP round trip at a time, with a read before
every write so that re-runs are safe.

## Measurements

### Latency to the site (from a laptop in the US, to staging)

| Request | Median |
| --- | --- |
| `GET user/me` (baseline round trip) | 286 ms |
| `GET activity/event/<id>` → 404 (existence probe) | 222 ms |
| `GET activity/event/<id>` → 200 | 380 ms |
| `GET activity/patrols/<id>` → 200 | 611 ms |
| `POST activity/patrols` | 430 ms |
| `POST activity/events` (one event) | **740 ms** |
| `POST observations` (81 points in one body) | **2,900 ms** |
| `POST subject/<id>/sources` | 338 ms |

The ~220 ms floor is network and TLS/auth overhead per request; das itself
answers a trivial GET in a few tens of milliseconds. Event creation is
expensive on the server side (schema validation, revision rows, realtime
notifications, analyzers), and so is observation ingest per point.

### One patrol, end to end

`import-patrols --patrol 050500_001453` (10 observations, no attachments,
81 track points) with every HTTP request timed:

| What | Requests | Time |
| --- | --- | --- |
| Preflight + bootstrap (once per run, not per patrol) | 22 | 7.3 s HTTP + ~11 s CPU loading the bundle JSON |
| `GET` patrol (404), `POST` patrol | 2 | 0.6 s |
| `GET` each event (404) | 10 | 2.3 s |
| `POST` each event | 10 | 7.9 s |
| `GET` source, `POST` source, `POST` assignment | 3 | 0.8 s |
| `POST` observations (1 batch, 81 points) | 1 | 2.9 s |
| **Per-patrol total** | **26** | **≈14.5 s** |

Albay's average patrol has 9.2 observations (median 6, max 90), 0.38
attachments and 187 track points (median 112, max 4,067), one leg. That
gives roughly 24 requests per patrol and the observed ~23 s once larger
tracks and attachments are included.

### How the per-patrol time splits (estimate, from the trace)

- **~55 %** event creation (`POST activity/events`, one per observation)
- **~15 %** event existence probes (`GET activity/event/<id>`)
- **~20 %** track observations (one `POST observations` per leader per leg,
  cost growing with point count)
- **~10 %** patrol, source and assignment calls

Attachments (0.38 per patrol, multipart upload) are a small share for Albay
but matter for photo-heavy sites.

### What is *not* the bottleneck

- Retries and errors: none in the run. The `ER returned bad response` lines
  are das answering 409 to an all-duplicate observation batch (duplicate
  SMART patrols with identical tracks), which the tool tolerates.
- The ledger: a JSON rewrite per stage, milliseconds.
- Bundle parsing: ~11 s once per run, then in memory.
- Preflight: ~7 s once per run.

## Why it is built this way

The design (see the migration spec) put **idempotence and safety first**:
deterministic ids derived from SMART uuids, a read before each write so a
re-run writes nothing new even without the ledger, one patrol at a time so
a failure is contained and the ledger stays simple, and the stock
`earthranger-client` (synchronous `requests`). That was the right call for
correctness on the first live runs; it is also exactly what makes the run
slow.

## Options

Ordered by expected payoff per unit of work. Gains are estimates; they
compound only partially because several target the same seconds.

### 1. Run patrols concurrently (client-side) — biggest win

Patrols are independent: distinct ids, distinct segments, distinct events;
track sources are per leader and already looked up or created idempotently.
A `ThreadPoolExecutor` with N workers over the patrol list, one `requests`
session per worker, turns 23 s/patrol into ~23/N s effective.

- Expected: **4–8× faster** with 4–8 workers (9.5 h → 1.2–2.4 h), assuming
  das keeps up. Staging and production das run multiple API replicas; event
  creation is the heaviest call and would be the first to show strain.
- Work: moderate. The ledger needs a lock (it is one JSON file rewritten on
  every stage); `_reactivate`/`_retire` stay single-threaded around the pool;
  the `sources` cache becomes shared with a lock; `counts` becomes
  thread-safe. The `AsyncERClient` in erclient is an alternative to threads
  but would mean rewriting `ERPatrolAPI`; threads reuse everything.
- Risks: 429/5xx under load (add backoff and a `--workers` flag defaulting
  to 1 or 4); two patrols sharing a leader creating the same source at the
  same instant (das rejects the duplicate; catch and re-read). Log lines
  from different patrols interleave; prefix them with the patrol id.

### 2. Create a patrol's events in one request — second biggest

`POST /api/v1.0/activity/events` already accepts a **list** and creates all
of them in one transaction (`das/activity/views/events/base.py`,
`EventView.create`). The tool sends one event per request today.

- Expected: for the average 9-event patrol, ~9 × 740 ms → one request of
  maybe 2–4 s: **~2× on the event share, 30–40 % per patrol**. Also one
  revision/notification fan-out instead of nine.
- Work: moderate. Waypoints with several observations create a collection
  parent plus children (263 waypoints in Albay); children reference the
  parent, so either post parents first (one request) then children (one
  request), or check whether das resolves the relationship within one list.
  Attachments stay separate requests (multipart, one per file). On a 400
  the whole list is rejected: fall back to per-event posts for that patrol
  so one bad observation does not block the rest.
- Note: das's `activity/patrols/<id>/segments/<seg>/events` variant stamps
  the segment on every event in the list; not needed since the tool sets
  `patrol_segments` itself.

### 3. Stop probing each event before creating it

Ten `GET activity/event/<id>` per patrol exist only for re-runs without a
ledger. Two cheaper ways to get the same guarantee:

- The patrol `GET` that already happens returns its segments **with their
  event ids** (`patrol_segments[].events`). For an existing patrol, that one
  response says which events exist; for a 404 (fresh patrol) none can exist,
  since event ids derive from the patrol's SMART uuid.
- Or, if das answers a duplicate-id `POST` with a clean 400/409, create
  blindly and treat that as "existing".

Expected: **removes ~15 % per patrol**, zero risk to idempotence. Work: small.

### 4. Cache sources and assignments across patrols

`_import_tracks` rebuilds its `sources` dict for every patrol, so each patrol
pays a `GET sources` (and sometimes a `POST`) for a leader that was looked up
a moment ago. Hoist the cache to the run. Assignments
(`POST subject/<id>/sources` with a time range) are per leg and needed, but
a leader's consecutive legs could be merged into one assignment range if das
accepts overlapping ranges (check; it may reject them).

Expected: ~1 request per patrol, **~3–5 %**. Work: trivial.

### 5. Run from inside the cloud region

The 220 ms request floor is mostly distance to the site. From a GCE VM or
Cloud Run job in the site's region the floor is ~10–30 ms. For GET-heavy
parts that is **5–10×**; for the heavy POSTs (server time dominates) it is
much less, so overall perhaps **1.5–2×** on today's code and less once 1–3
remove the chatty calls. Also the natural home for a job that runs for
hours: no laptop sleep, no VPN drops. smart-able already has a Cloud Run
job (`web/`) that could host it, or a plain VM with the bundle on GCS.

### 6. Tune the observation batches

One `POST observations` of 81 points took 2.9 s; the 4,067-point tracks are
posted in 1,000-point batches. Measure the per-point cost on das: if it is
linear, batch size does not matter and the only lever is concurrency; if
there is a large per-request fixed cost, bigger batches help. Check the
request-size limit before raising `OBSERVATION_BATCH`.

### 7. Two-pass import for visibility, not speed

`--skip-tracks` first (patrols and events appear in the UI within ~1/3 of the
time), then a second run for tracks. The ledger's per-stage records make
this safe today with no code change. Same total time, earlier feedback.

### 8. das-side changes (follow-up tickets, long term)

The migration spec already lists a **bulk patrol import endpoint** as a das
follow-up. Two cheaper server-side helps:

- A request header or flag for imports that **defers realtime
  notifications, analyzers and activity-feed fan-out** on event create. Much
  of the 740 ms is side effects a historical import does not need.
- `POST activity/patrols` accepting **segments with events inline**, so a
  patrol with its events is one request, in one transaction.

### 9. Micro-items

- `PATCH` of 90 retired subjects at start and end of every run: 180
  requests ≈ 1 min, only noticeable on short runs.
- Preflight re-runs its full read of subjects, event types and choices on
  every invocation (~7 s): fine.
- Bundle load ~11 s of CPU (parsing 285k track points from JSON): fine per
  run; would matter if the import were split into many small invocations.

## Recommendation

Do **3, 4, 2, 1 in that order**: 3 and 4 are an afternoon and remove the
pure overhead; 2 halves the dominant cost; 1 multiplies whatever is left.
Together, a conservative estimate for Albay is **~23 s → ~8–10 s per patrol
sequentially, and ~1.5–2.5 s effective with 4–6 workers: roughly 40–60
minutes instead of 9–10 hours.** Keep `--workers 1` as the default until a
production das has been watched under the concurrent load, and keep the
read-before-write path as the fallback when a bulk request is rejected.

Option 5 (run it in the region) is operational rather than code and is
worth doing regardless for multi-hour jobs. Option 8 is where the next
order of magnitude lives, but it needs das releases; file the tickets now
so they ride along with the other patrol-API follow-ups.

## Verifying any change

`scripts/time_one_patrol.py` wraps `requests.Session.request`, imports a
single patrol through the real CLI into a throwaway ledger, and prints the
requests grouped by method, status and path with counts and medians.
Before/after on the same patrol is the honest benchmark; the full-run rate
(patrols per minute, from the ledger's `stages`) is the acceptance test.

| One patrol, 10 observations | Requests | Wall | In HTTP | Of which |
| --- | --- | --- | --- | --- |
| Before (0.7.0, 2026-10-14, Albay `050500_001453`, 81 points) | 45 | 32.7 s | 21.7 s | 10 × `POST events` 7.9 s; 10 × `GET event` 2.3 s; 1 × `POST observations` (81 pts) 2.9 s |
| After Tasks 1–4 (2026-10-15, Guinobatan `050504_000679`, 286 points) | 26 | 43.2 s | 43.1 s | 1 × `POST events` (10 in one list) 5.6 s; 0 probes; 1 × `POST observations` (286 pts) 24.5 s |

The two rows are different patrols on different days, so compare the
per-item costs, not the totals: the client-side work shrank as designed
(45 → 26 requests, no probes, one request for all events), but das's
server-side cost per item did not — about 0.5 s per event whether posted
singly or in a list, and 36–86 ms per track point depending on the day
(phinney answered `GET user/me` in 286 ms on the 14th and 360–800 ms on
the 15th while an import was running).

## Results of the live benchmark (Task 6, phinney, Guinobatan `050504`, 2026-10-15)

| Run | Patrols | Rate |
| --- | --- | --- |
| Albay full run, 0.7.0, 2026-10-14 | 1,527 | ~2.0 patrols/min average over 12.5 h (1.4–3.9 by stretch) |
| Window A, new code, `--workers 1` (2022-02..06) | 11 of 59 before interrupt | ~0.56 patrols/min |
| Window B, new code, `--workers 4` (2024-02..09) | 22 of 59 before interrupt | ~0.6–0.7 patrols/min, 0 × 5xx/429 |

Guinobatan's patrols carry more points per patrol than Albay's (447 and
360 in the two windows against Albay's 187 average) and the site was two
to three times slower on the 15th, which is why the absolute rates are
below the Albay run. The comparison that matters is A against B on the
same day: **four workers gave at most 1.2× the sequential rate, with no
429s or 5xx.** das is serialising the heavy writes; the bottleneck is on
the server, not between client and server.

Three other things the live run confirmed:

- Ctrl-C with workers: a foreground run exited ~12 s after SIGINT and
  retired the 24 reactivated members (ledger `reactivated` back to 0). A
  run started with `nohup … &` from a non-interactive shell ignores SIGINT
  entirely — the shell sets SIGINT to ignored for background children and
  Python then installs no handler — so stop such a run with SIGTERM, and
  prefer a foreground or `setsid` launch for long imports.
- Re-runs: two finished patrols re-ran as `already_imported`, writing
  nothing; a patrol interrupted after its events stage resumed with only
  its tracks stage.
- No new failure modes: the bulk event path never fell back to single
  posts on either window.

### Where the server time goes (from the das source, `release-2.148.1`)

- `das/observations/signals.py:71-95` — for every saved observation whose
  `recorded_at` is older than seven days, that is every historical point an
  import writes, the `post_save` handler runs a `SubjectSource` lookup and
  enqueues one `maintain_subjectstatus_for_subject` Celery task
  `on_commit`. A 286-point track means 286 tasks; the Albay import queued
  roughly 270,000. The list endpoint also inserts row by row
  (`ListSerializer.create`), and a single duplicate in a batch drops the
  whole batch to the one-row-per-transaction fallback
  (`das/observations/views/observations.py:199-235`).
- `das/activity/signals.py:52-70` — every created event publishes a
  realtime message and enqueues an `evaluate_alert_rules` task; the Albay
  import queued about 14,000 alert-rule evaluations on historical data.

Those Celery floods also load the API's own database while the import
runs, which is consistent with the site slowing down during imports and
with concurrency buying nothing.

### Revised recommendation

The client-side work (Tasks 1–4) is done and worth keeping: fewer
requests, no probes, a `--workers` flag that will pay off on a das that
parallelises, and the safety properties intact. The next order of
magnitude is server-side and needs das tickets:

1. **An import mode for historical data** — a request header or a
   `provenance`/`source` flag that das honours by skipping per-record
   side effects that make no sense for events and points years old:
   `evaluate_alert_rules`, realtime publish, and per-point
   `maintain_subjectstatus_for_subject`. One subject-status refresh per
   subject at the end of the batch is enough.
2. **Deduplicate `maintain_subjectstatus_for_subject` per subject per
   transaction** for the historical branch of `observation_post_save`, the
   way `observation_segment_post_save` already buffers its work. This
   helps every bulk observation producer, not only this tool.
3. **Bulk insert for observation lists** (`bulk_create` with the unique
   constraint handled per row) so a list request costs one round trip to
   the database.
4. Then re-measure with `--workers 4`; if das still serialises, the API
   replica or database connection configuration on staging is the next
   place to look.

Until those land, plan on roughly 2 patrols a minute per site regardless
of client settings, run imports in the site's cloud region from a
foreground or `setsid` process, and use `--skip-tracks` first when the
patrols and events need to be visible early.

### Why threads, not asyncio

The import spends almost all its time waiting on responses, which is the
usual case for asyncio. It uses a thread pool instead, for three reasons:

- **asyncio would not make it faster on today's das.** Four threads already
  kept four requests in flight and gained at most 1.2×; das serialises the
  work behind each request. asyncio's advantage is holding many more
  requests open cheaply, and more open requests would only queue on the
  server (and load a production site that is already the bottleneck).
- **At this scale the two cost the same.** A useful ceiling is 4–16
  concurrent patrols, bounded by what das absorbs. Threads at that count
  are cheap, and the GIL is released while they wait on the network.
- **The rewrite is large.** erclient's `AsyncERClient` lacks `get_subjects`
  and `post_subject`, and everything above it would become `async`:
  `ERPatrolAPI` (shared with `patrol-datamodel` and the CLI), the import
  stages, preflight's reads, and the test fakes. Ledger writes and bundle
  parsing would still need `asyncio.to_thread`. The threading cost is one
  lock on the ledger, one on the source cache and a per-thread logging
  filter, each covered by tests.

Revisit once das parallelises (item 4 above). Try more workers first; move
to asyncio if the goal becomes much higher fan-out, such as many patrols'
tracks uploading at once, or overlapping work within one patrol, such as
posting its track while its events are still being written.
