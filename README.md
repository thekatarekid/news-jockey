# News Jockey

A local news collector and browser portal for a Mac. Flask and SQLite store publisher coverage, articles, full-text extraction, images, video metadata, and saved article versions on this computer. No cloud database is required. Local processing needs no account; optional Luna/Terra decisions use the existing ChatGPT sign-in.

## Run

Double-click `start.command` in Finder. It opens **http://127.0.0.1:8765** and starts a Mac background service. You can close the Terminal window and the browser; collection, discovery, and extraction keep running while the Mac is awake. The service starts at login and restarts after a process exit.

From this project directory, check or stop the service:

```sh
.venv/bin/python tools/portal_service.py status
.venv/bin/python tools/portal_service.py stop
```

Stopping also removes login startup until you run `start.command` again. The service registration is `~/Library/LaunchAgents/com.newsjockey.portal.plist`; current service logs are in `~/Library/Logs/NewsJockey/service.log`.

For foreground debugging, stop the background service first, then run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

The existing environment is prepared. The app binds to `127.0.0.1`, with debug/reloader disabled. `--no-poll` disables all background workers for debugging.

For foreground runs, leave the Terminal open and press **Control-C** to stop. A portal opened in the browser needs one of these server processes running; the browser alone does not serve it.

## Articles

The table displays publisher, **source category**, **content type**, **media formats**, publication/collection times, author/tags, saved body quality/origin, and latest fetch outcome. It defaults to publication dates in the current calendar month in the portal timezone (initially Asia/Calcutta). Filters cover these fields, search, and date ranges; sorting and pagination remain available.

- Source category describes the publisher's primary focus. It never restricts discovery to that category.
- Content type describes editorial function: reporting, editorial, opinion, analysis, review, interview, explainer, live blog, sponsored, other, or unknown.
- Media formats are separate: text, video, audio, and image gallery can coexist.
- Explicit publisher labels, URL sections, headline labels, and structured data supply the initial content type. Unknown stays unknown; Phase 3 can refine labels with evidence and manual review.
- Open a title for collected text, original feed text, extraction details, publisher images with attribution, video/audio links, transcript status, discovery channels, and saved versions.
- Use **Save label** for a manual content-type correction. Extraction and feed updates preserve it.
- **Extract again** queues the article for another attempt. Publisher/all-publisher retry controls queue recent gaps; an explicit retry of an older article creates a manual job.

### Full-text status

| Status | Meaning |
|---|---|
| pending / extracting | Waiting for / undergoing page retrieval |
| complete | A readable public body meets structural completeness checks; not publisher-verified |
| partial | Public text was found but completeness is uncertain or truncation is indicated |
| paywalled | A visible restriction or metadata marker accompanies an incomplete body; public/feed text remains |
| blocked | Access challenge, HTTP 401/403/429, or similar restriction |
| failed | Transient/network or processing failure; bounded retry is scheduled |
| unsupported | No usable body or unsupported document format |
| not_applicable | Video/audio item without an article body; transcript status is separate |

**Feed content**, **Feed summary**, and **Headline only** describe the feed payload, not article completeness. Successful extraction may show **Full text** or **Partial page text**. Longer original feed text remains accessible independently.

Extraction compares versioned publisher CSS/XPath recipes, Trafilatura, Readability, cleaned body roots, and public structured text, including alternatives to partial results. Feed bodies can skip page retrieval after three distinct feed/page matches per channel/template, with six-hour spot checks. Original feed HTML, sanitized rich HTML, and plain text are separate. Failed or shorter rechecks retain a better saved body.

Completeness uses paragraphs, structure, truncation, publisher word counts, and feed comparison; it remains a heuristic. A metadata restriction marker alone does not discard a complete visible body delivered in ordinary HTML. Actual restriction/challenge pages remain gaps; hidden restricted structured bodies are excluded. Videos retain metadata, embedded transcripts, and public English/default VTT/SRT captions when available; no media download or automated transcription. Caption failures do not fail the article.

The extraction section separates publisher body availability from fetch/job states, with date-group tables, host cooldowns, and attempt counts/timing. Legacy plain text survives migration as safe paragraph markup with unknown quality until revalidated; original formatting cannot be recovered from that old text alone.

## Publishers and discovery channels

The portal still starts with the original 50 feeds and searches the curated catalog. Existing extra sources are preserved. A publisher can now have many feed/sitemap channels.

- Add a catalog suggestion or discover website/feed URLs in batches of up to 50.
- Background discovery validates broad feed hints, advertised feeds, linked feed directories, and robots-advertised sitemaps. New channels become visible in Sources.
- **Publisher coverage** shows channel counts, scan times, gaps, and complete-text counts. **Scan** / **Scan all publishers** queues a new scan; enabled publishers are rescanned daily.
- Source category changes apply to the publisher and its channels.
- Channels remain independently enabled/disabled, with their own interval, TTL, status, and backoff.
- **Show channels for** narrows the channel table to a publisher.
- Overlapping feeds/sitemaps produce one article per publisher, retaining each channel's identity. Cross-publisher reporting remains separate.
- Historical duplicates keep their database IDs as aliases; no old articles are deleted.

Coverage is bounded and visible, not a promise of every publisher article. Feed scans check up to 40 candidates and eight sitemap channels. Sitemaps collect recent seven-day and undated inventory, cap at 1,000 URLs per check, and rotate through index children. Traversal follows nested indices with a three-index-level and 20-file request limit; gaps are shown. Profile/directory pages are excluded. PBS NewsHour stays scoped to `/newshour/`, rather than all PBS television archives. Official news APIs, licensed-content integrations, paid search/scraping, proxies, hosted browsers, and per-article model extraction calls are excluded.

### Schedule and recovery

- Scheduler checks due channels every 15 seconds.
- Feeds default to five minutes; sitemaps default to 30 minutes.
- Feed TTL, conditional requests, exponential backoff, and `Retry-After` are respected.
- Up to six collection requests run concurrently; coverage scans use separate workers.
- Nine extraction workers: four automatic current-month HTTP workers, four manual HTTP workers, and one explicit Chrome worker. HTTP work shares host cooldowns and avoids simultaneous requests to one host.
- **Automatic:** articles published in the current calendar month, in the editable portal timezone. The month changes automatically at local month rollover.
- **Extract selected month:** choose any stored publication month (or unknown dates) and queue all its articles.
- **Extract all previous months:** all dated articles before the current month. Unknown dates remain separate.
- **Chrome fallback: Selected month** and **Chrome fallback: All previous months:** optional explicit browser jobs using those same groups. Neither starts automatically.
- Already complete saved bodies satisfy ordinary jobs; they are distinguished from new extraction attempts. Blocked/restricted results remain gaps, and host cooldowns can delay job completion.
- Jobs snapshot all matching retained IDs, independent of table filters/pagination; controls show counts and pause/resume/cancel. Duplicate active jobs are reused. Existing jobs retain their original window after timezone/date changes.
- Dead-process leases recover at restart; other expired leases recover after ten minutes. Paused/queued job state survives restart.
- Collection parses feeds before taking the database writer lock and commits large inventories in batches of 25.
- Raw page cache: seven days, 200 MB target, hourly pruning, ETag/Last-Modified revalidation. Saved article HTML/text and versions remain separate from this disposable cache.
- Feed/sitemap discovery cannot guarantee recovering every article missed during downtime.

### Optional browser fallback

Use one of the two **Chrome fallback** buttons. Ordinary automatic/archive jobs never start Chrome. The local Python service launches the installed Chrome executable as a separate headless process with a temporary context/profile and closes it after the request. Your normal Chrome can be closed; existing tabs, profile, cookies, and login sessions are not used. Publisher paywalls and challenges can still prevent recovery. No publisher-wide toggle enables implicit rendering.

### Recipe maintenance

- **Extraction recipes** lists candidate/validated versions and repeated template failures. Initial selectors for 55 publishers plus a live-blog recipe remain candidates until checked on their current templates.
- Export retained HTML/diagnostics from a publisher, edit declarative JSON, preview on separate validation article IDs, then validate and activate. At least two held-out validation IDs must differ from the development IDs. Activation requeues matching recent items; archives wait for manual jobs. Previous versions can be restored.
- CSS/XPath include/remove, hostname/path/DOM matching, metadata selectors, bounded browser waits, and optional same-host public variants are supported. No executable recipe code is accepted.
- Diagnostic exports can support occasional Luna/Terra-assisted repair. Extraction makes no model request and needs no model key.

## Images

All discovered publisher article images and video thumbnails are retained, including those without recorded reuse permission. Details show publisher/source links, captions, and photographer/agency credits when supplied. Missing credits are not invented. Licensing metadata is optional and does not block collection. Cover selection and story/topic presentation belong to later phases.

## Timestamp meanings

Known zoned timestamps normalize to UTC. Ambiguous publisher strings remain raw. The UI displays the configured portal timezone. Calendar-month groups use that timezone; explicit from/through fields remain labeled UTC.

| Field | Meaning |
|---|---|
| Published / publisher updated | Publisher-provided times, from feed or explicit page metadata |
| First seen / last seen | Collector discovery times; a cached 304 does not update item last-seen |
| Content changed | Most recent observed text/metadata change |
| Extraction attempted / extracted | Retrieval attempt / processed public-page result |
| Version observed | Time the collector saved a changed article snapshot |
| Event time | Phase 3 evidence-linked event date/time, approximate or unknown; distinct from publisher/platform timestamps |

## Data, documentation, and checks

- `data/newsjockey.sqlite3`: local database, ignored by Git.
- `data/backups/`: pre-migration backups for extraction and Phases 3–5.
- `~/Library/Logs/NewsJockey/service.log`: current background server diagnostic log.
- `data/runtime.log`: historical logs from foreground runs started by Codex.
- `news_processing.md`: phased specification.
- `progress.md`: implementation history, verification, current state, and remaining work; update it as work progresses.
- `SOURCES.md` and `catalog.json`: starter source rationale and suggestions.
- `newsjockey/`: storage, network, collector, discovery, metadata, and extraction.

To back up manually, stop the server and copy `data`, or use SQLite's backup API for an active database.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary databases and mocked publisher/caption responses. The updated suite has 99 passing tests. An isolated real Chrome job also saved a full public body. A dated structural audit is in `artifacts/extraction-audit-20261006/audit.md`; it is not an independently verified >90% benchmark.

Run another free sample audit (five recent items per publisher, with failed-host requests deferred):

```sh
.venv/bin/python tools/audit_extraction.py --output artifacts/extraction-audit-next
```

 They cover original collection behavior plus migration preservation, overlapping channels, content/media types, images, page extraction, paywalls, retries, saved versions, sitemap traversal, and request protection. Phases 3–5 add model evaluation, news categories and stories/topics. Phase 6 summaries/views and Phase 7 publication review remain planned.


## Categories and whole-month stories (Phases 3–4)

1. Open **3 · Categories & entities** or **4 · News stories**. Select a publication month and click **Process whole month into stories**. This snapshots every eligible stored article in that month, without the old 25/500-item cap.
2. Review persistent progress: local metadata and embeddings, whole-month candidate retrieval, and story grouping. Pause/resume/cancel persists. Articles arriving later enter the next run; unchanged features are reused.
3. Review metadata with exact model input and supported entity spans. Source category remains separate from inferred multi-label news categories. Content types retain publisher evidence. Event time stays unknown unless explicitly reviewed; publication time is separate.
4. Review stories by month/search/group size with pagination. Multi-article, multi-publisher and single-article counts are separate. Split/merge/reassign memberships and metadata corrections remain available.
5. Topics are deferred until same-event story quality is reviewed. Phase 6 factual outputs remains planned. Representative publisher headlines are not generated factual headlines.

**Default pipeline is entirely local:** GLiNER2 (`fastino/gliner2-base-v1`, 205M parameters) for entities and categories; EmbeddingGemma 2 for separate normalized 512-dimensional title and title+lead embeddings; NumPy cosine retrieval plus weighted event words, entity overlap, recurring-event guards and representative/cohesion checks for clustering. Matching input is title plus at most 1,100 lead characters from the best retained body/transcript; GLiNER2 uses the title and first 500 lead characters. Full extraction is attempted independently and is not a prerequisite for clustering.

No Luna/Terra calls run in whole-month jobs. Existing subscription adapters remain available for deliberately bounded future diagnostics/review, with tools disabled; no Sol/Astra, paid inference, publisher API, paid search or extraction service is enabled.

Local model setup (already prepared on this Mac):

```sh
.venv/bin/python tools/setup_embeddings.py
.venv/bin/python tools/setup_nlp.py
```

The NLP model runs from a downloaded snapshot in an isolated Python 3.12 environment with Hugging Face offline mode. Embeddings use app-owned Ollama at **127.0.0.1:11435**, cloud disabled. Neither uses the personal Chrome profile. Downloaded models remain in `data/models`. Jobs checkpoint every batch and record model revision, embedding digest and input hashes.

October rebuild evidence and reports: `artifacts/october-stories/`. The earlier 23-article experiment was backed up and cleared; raw data and versions were preserved. Clusters are provisional until real examples, false merges and missed matches are reviewed. No clustering ratio or precision figure is promised.

### Extraction jobs and steady state

- A month has one ordinary extraction job, whether selected as Current month or by its month name. Repeated clicks reuse that job and append newly discovered articles, including after it completes.
- Historical extraction is a one-time backfill, not a recurring full-archive scan. Completed work is retained. Overlapping month/archive requests share saved outcomes for unchanged articles, including partial or unavailable results; use **Extract again** for an intentional retry.
- Automatic current-month processing continues for new/changed feed items with existing retry backoff. Completing or revisiting a historical job does not start a fresh pass over the archive. Chrome remains a separate explicit transport.
- The job table shows **Reused results** (saved bodies or recorded attempts), and identifies pending work waiting for host cooldowns. Consolidated duplicate job records remain in the database for audit but are omitted from the active job table.

## License

News Jockey is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

Third-party dependencies, model weights, and publisher content retain their own licenses and terms.
