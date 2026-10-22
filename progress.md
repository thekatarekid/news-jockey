# News Jockey — Progress

## Current state — Phases 1–5 implemented, 7 October 2026

- **Updated Phases 1–2: implemented and verified.** Existing portal runs at http://127.0.0.1:8765 as a Mac background service. Rich HTML/plain text, body/fetch provenance, validated feed reuse, recipes, captions, persistent jobs, host cooldowns, and revised tables are active. Phases 3–5 now have separate metadata, story and topic screens, with local EmbeddingGemma 2 and explicitly requested Luna/Terra subscription jobs. Phases 6–7 remain planned.
- **Automatic extraction:** publication dates today + yesterday in the portal timezone (default Asia/Calcutta), recalculated at midnight. Older/undated items wait for **Extract old articles**. Two explicit Chrome buttons use the same recent/older groups. No automatic browser launch or archive extraction.
- **No paid extraction:** no paid search/scraping/proxies/hosted browsers/news APIs/licenses or per-article models. Optional recipe diagnosis exports/imports support occasional model assistance. Extraction makes no model request; explicit Phase 3–5 batches may use the signed-in ChatGPT subscription.
- **Verification:** 99 automated tests pass. Metadata/story/topic review controls and live tables were checked; all backup IDs are preserved, SQLite quick-check and foreign-key checks pass. A 23-article sample produced 18 stories and 31 topic seeds; one changed article remains stale. This is not a production-quality benchmark.
- **Coverage:** first audit targeted five recent items per publisher: 264 items across 53 of 55 publishers (two had no inventory in the audit window). After refinements, 203 complete, 1 partial, 9 blocked, 1 failed, 9 restricted, 3 video-only, 38 deferred following host failures. 203/261 sampled text-bearing items passed structural checks (~77.8%); this is not independently verified completeness and does not establish >90% coverage. See `artifacts/extraction-audit-20261006/audit.md`.
- **Operational work remains:** normal recent workers continue revalidating the stored backlog; many legacy bodies are deliberately unknown. More publisher/template validation is needed, and inaccessible bodies/inventory gaps remain explicit. Historical checkpoints below describe the behavior at their original dates and are superseded by this current state.

## Past progress

### 2 October 2026

- Built the original local Flask/SQLite collector and portal, with 50 starter sources across five categories, publisher suggestions, bulk URL discovery, article tables, filters, and scheduling.
- Inspected polling: five-minute configured defaults, publisher TTL, conditional requests, six feed workers, and error backoff.
- Researched event/topic grouping, embeddings, decision models, hosted generation, and supported CLI adapters. Published cost figures in the specification as a dated snapshot, not measured operating costs.
- Agreed to collect all English sections; keep source category separate from multi-label news categories; retain full-text gaps visibly; support automatic topics and three review modes.

### 4 October 2026

- Created `news_processing.md` with seven implementation phases, deliverables, quality targets, and evaluation scenarios.

### 6 October 2026 — specification refinements

- Moved reporting/editorial/opinion/analysis/review labeling into Phase 1.
- Added separate media-format fields for video-only and mixed-media items, plus transcript availability.
- Revised image handling to collect publisher images regardless of recorded reuse permission, with attribution. Updated later phases and acceptance criteria consistently.
- Portal implementation had not yet changed for these refinements.

## Work log

- Started implementation of Phases 1 and 2. Preparing additive migrations, broader discovery, persistent extraction jobs, article versions, and portal controls.
- Created a SQLite backup in `data/backups/` before modifying the live schema.
- Installed pinned extraction dependencies into the existing `.venv`: Trafilatura, Readability, defusedxml, and Playwright. Existing Chrome can support optional public-page rendering.
- Implemented publisher/channel relationships, historical-ID-preserving duplicate aliases, per-channel article identities, article versions, and persistent extraction state.
- Implemented explicit publisher content labels, separate media formats, feed/page images, video references, and transcript availability.
- Added validated broad feed hints, homepage/feed-directory discovery, robots-advertised sitemaps, and bounded recent sitemap collection with visible gaps.
- Added independent article extraction workers, retry/backoff, preserved feed text, structural completeness checks, and optional fresh-context browser rendering.
- Added publisher coverage, extraction status/retry, content-label correction, and article-version APIs. Running baseline tests before portal updates.
- Updated article filters/details and Sources view with publisher coverage, channel types, content/media labels, extraction queue counts, retry controls, attributed images, video links, transcript status, and saved-version access.
- Applied the live migration: all 11,631 original article IDs and 56 subscriptions preserved; 55 publisher records created; seven historical overlapping-channel articles aliased without deletion. Foreign-key checks passed.
- Fixed a feedparser enclosure alias issue caught by the video-only test. Original 15 tests plus 22 new processing/migration tests now pass.
- Started the local app with automatic coverage discovery and article backfill; live checks are in progress.
- Live article retrieval succeeded for CBS News, Fox News, NPR, TechCrunch, and Engadget. A Verge sample was explicitly paywalled and an Ars Technica sample returned HTTP 405; these gaps remain visible.
- Live inventory checks exposed author/player profile pages in publisher sitemaps. Added directory/profile filtering and corrected those items' extraction classifications instead of treating them as complete reporting.
- Initial expanded discovery has already added channels beyond politics-only subscriptions. Discovery and historical backfill continue in the background; neither complete publisher coverage nor full text behind access controls is claimed.
- Completed the first coverage scan of all 55 current publishers; 376 validated feed/sitemap channels were present at this checkpoint, up from 56 original subscriptions. Channel counts can continue changing after subsequent scans.
- Added nested sitemap traversal, rotating bounded inventories, PBS NewsHour brand scoping, and rejection of profile/directory pages. Historical records are retained even when excluded from the news table.
- Prioritized feed-backed articles and recent changes ahead of undated sitemap backfill. Specific opinion/analysis/review labels take precedence over generic News labels; manual corrections survive concurrent extraction.
- Tested the optional browser renderer against a public page using the installed Chrome in a fresh headless context.
- Expanded verification to 43 passing tests, including restart recovery, canonical merging without ID deletion, nested sitemaps, video landing-page handling, and restricted structured-body exclusion.
- Updated README.md, PROJECT_BRIEF.md, and the specification status to match the implementation.
- Verified the portal's article filters, publisher coverage table, full-text details, attributed images, video links, and saved-version controls in the local browser.
- Final robustness check caught nested hidden HTML elements breaking extraction. Fixed parent/child removal handling and added a regression case. Stop background schedulers before interpreter shutdown when the local server exits.
- Final test run: 44 tests passed. Restarted the server with the final code and confirmed article/processing/publisher APIs respond successfully.
- Saved a portal preview to `data/phase-1-2-portal.jpg`; left the running portal open at `http://127.0.0.1:8765/`.

### Extraction stats refinement — 6 October 2026

- Inspected current extraction gaps: HTTP 403 refusals mainly from Seeking Alpha and Politico, plus Bloomberg, The Hill, and NYT; HTTP 401 responses from WSJ. The current failed item returned HTTP 405 from Ars Technica. These are observed responses, not a confirmed diagnosis of each publisher's access policy.
- Partial means some text was found but completeness checks did not pass. Observed examples include Yahoo Sports pages yielding less text than their feeds, and 9to5Mac bodies shorter than publisher word counts. Some short or unusual pages also fail structural checks despite potentially having all their text.
- Replaced the single-line extraction stats with a table containing counts and plain-language meanings for every status, plus total articles and a separate saved-version metric. Counts remain global and refresh every 15 seconds.
- Clarified saved versions in the table and article details: snapshots include initial collection/migration, extraction enrichment, feed changes, and manual labels; they do not all indicate publisher edits.
- Verification complete: JavaScript syntax check passed; Flask renders the new table; the live browser shows all nine statuses (including zero counts), explanations, article totals, and saved-version totals. The displayed status counts sum to the total articles. Existing 15-second polling remains active.
- Restarted the local server to serve the updated template. Saved the verified preview to `data/extraction-stats-table.jpg` and left the portal open.

### Full-text availability investigation — 6 October 2026

- The user's screenshot has 69,301 queued items and 1,357 finished extraction results; queued inventory is not an extraction failure. Among finished results, 376 were complete and 370 partial, so full-body quality also needs work.
- Current collection gets links and text from RSS/Atom plus URLs from publisher sitemaps, then requests article-page HTML. RSS payloads vary: Fox News and ProPublica samples carry bodies closely matching public page extraction; many others only provide excerpts. Long Politico/Business Insider feed text remains stored even when the page request is blocked/restricted.
- At audit time, about 54,400 pending items came solely from sitemaps and lacked publication dates; about 14,900 were feed-backed. Two workers cannot clear that growing inventory promptly. Keep current news and historical inventory distinct when scheduling and reporting progress.
- Confirmed false restriction signals: generic matching classified The Register's `hidePaywallLabel` and Entrepreneur's `paywall-open` classes as paywalls. Structured publisher access restrictions must remain respected.
- Confirmed completeness bug: the first `<article>` on a 9to5Mac page was a sidebar card, while the extractor had found the main text. Body checks used that wrong element.
- Queue selection measured 7.2 seconds for two IDs on the current database. Replaced the backlog-wide priority sort with small ordered feed/retry/archive selections and a partial pending-order index. SQLite still chose the broad status index initially; explicitly selecting the ordered index reduced measured selection to 0.0003 seconds, then below four-decimal timing resolution. This measures selection overhead, not article-download time.
- Fixed false paywall CSS matching and selected the headline's article/main for body checks and image extraction. Kept explicit structured restrictions and visible access prompts intact.
- Fresh live rechecks of articles 12805 (The Register), 45259 (Entrepreneur), and 12375 (9to5Mac) now return complete public text: approximately 413, 983, and 513 words. Stored those extraction results and queued 71 additional affected partial/paywalled records without deleting text or history.
- All 51 tests pass, including new regressions for open/hidden/empty paywall hooks, sidebar cards, feed priority, enabled channels, retry times, and duplicate-free queue selection. Existing restricted-body tests still pass.
- Applied the new index at service restart after a live schema attempt encountered an active SQLite writer. Restarted the existing launchd service with the final extractor; login/restart configuration remains intact.
- Final HTTP checks confirm the three corrected samples show `Full text` / `complete` in portal article responses; the service reports active workers. Foreign-key checks remain clean. A pending recheck can retain previously extracted full text, so extraction-state counts alone do not measure how many article bodies are available.
- **Investigation status: DONE_WITH_CONCERNS.** Confirmed parser and queue bugs are fixed and verified. Feed-first completeness/provenance, publisher-level retries, selective rendering for JavaScript bodies, and staged archive backfill remain follow-up improvements. Public feeds/pages cannot guarantee complete bodies for all 50 publishers.
- Reference: the [RSS specification](https://www.rssboard.org/rss-specification#hrelementsOfLtitemgt) allows synopsis or complete item text; [AP Media API](https://api.ap.org/media/v/docs/Getting_Started_API.htm) supplies licensed text/story content subject to the customer's contract, not every article from our selected publishers.

### Portal availability repair — 6 October 2026

- Reproduced the unavailable portal: no process listening on port 8765; local HTTP requests fail with connection refused. The previous command session is gone. Logs ended with successful requests, so the precise reason the old process exited is unrecorded.
- Replaced startup tied to a foreground command with a per-user Mac launchd service. `start.command` now starts that service, checks readiness, and opens the browser; Terminal/chat closure no longer controls server lifetime.
- Added explicit start/stop/status commands, login startup, process restart, and persistent log paths. The server remains bound to `127.0.0.1`.
- Corrected launcher dependency checks to include Phase 2 extraction packages.
- First launchd attempt failed before Python could initialize when stdout/stderr pointed into the protected Documents folder. Moving only service logs to `~/Library/Logs/NewsJockey/service.log` allowed startup; project and database locations remain unchanged. No privacy settings were changed.
- Confirmed the running server's parent PID is 1 (launchd), the starter command exited, and `/api/processing` responds with active workers. Browser and final regression verification continue.
- Verification complete: all 48 tests passed, launcher shell syntax passed, and the live browser loaded article rows and extraction stats. The starter command exited while the server remained under launchd (parent PID 1).
- Verified automatic recovery by terminating the service process once: launchd started a new process and the portal passed its HTTP readiness check. Collection and extraction resumed from the existing database.
- Saved the restored portal preview to `data/recovered-portal.jpg`. Startup/stop/status instructions and current service log location are documented in README.md.

## Verification and remaining work

- **Automated checks:** 51 tests passed using `.venv/bin/python -m unittest discover -s tests`. Coverage includes legacy migration, overlapping channels, editorial labels, video-only feeds, extraction, images, versions, retries, hidden/restricted bodies, browser fallback, restart recovery, service lifecycle handling, false paywall hooks, sidebar body selection, and indexed queue selection.
- **Live database checks:** SQLite integrity `ok`; zero foreign-key errors; all 11,631 original article IDs and 56 original subscription IDs preserved. Backup: `data/backups/before-phases-1-2-20261006-103146.sqlite3`.
- **Coverage checkpoint:** all 55 publishers scanned; 376 feed/sitemap channels registered. The original New York Times business and national channels belong to one publisher, explaining the publisher/subscription count difference.
- **Portal checks:** content/media/extraction filters, publisher coverage, full-text details, image attribution, video links, saved versions, and publisher sorting passed. Live public extraction and optional installed-Chrome rendering were also verified.
- **Backfill checkpoint:** roughly 45,000 visible items and 46,000 saved versions, including newly discovered inventory; about 44,500 items remain queued. Counts change as collection and extraction continue. Extraction completeness is heuristic and remains visible separately from feed text.
- **Remaining operational gaps:** some feeds/inventories are blocked or unavailable; paywalled bodies remain inaccessible; bounded sitemap traversal cannot establish every publisher's complete inventory. Video transcripts depend on publisher availability. Optional rendering must be enabled per publisher when useful.
- **Run behavior:** the Mac background service starts at login and restarts after process exit. Collection and extraction continue while it is running and the Mac is awake. Feed intervals respect publisher TTL/backoff; sitemaps default to 30 minutes; publisher coverage scans run daily. Persistent queues resume after restart, including recovery of interrupted extraction attempts.
- Phases 3–7 remain planned; no LLM processing, story/topic grouping, or summary generation is part of this implementation.

### Open-source reader research — 6 October 2026

- User excluded official publisher/news APIs and licensed-content integrations. This supersedes the earlier suggestion to use those for inaccessible content.
- Started inspecting primary documentation and source code for the six readers in the screenshot, plus extraction libraries and additional open-source tools. Research only; the running extraction pipeline is unchanged.
- Evaluating feed-supplied HTML, article-page fetching, publisher rules, generic parsers, rich-text preservation, caching, rendering, and external-service dependencies.
- Completed source inspection of Miniflux, FreshRSS, NetNewsWire, Fluent Reader, NewsBlur, and Feedbin; also inspected Read You, Wallabag/Graby, RSS-Bridge, morss, and yarr, plus Mozilla Readability, Mercury, and FiveFilters rules.
- Verified local parsing in Miniflux/FreshRSS/Fluent Reader and separate extraction services in NetNewsWire/Feedbin. NewsBlur combines a self-hostable Mercury service with Python Readability and has an optional configured paid proxy fallback.
- Inspected FiveFilters' 2,049 text configurations and Mercury's 207 domain extractor files. Exact hostname configurations exist for 29 of the current 55 publisher records; this is rule availability, not confirmed current extraction or access coverage. Some inspected configurations contain old test URLs or only cleanup directives.
- Found yarr's inspected default-branch crawl handler has page downloading commented out; excluded it as an immediate working extractor recommendation. Development snapshots are distinguished from released applications in the research document.
- Confirmed the existing collector/extractor flatten feed and page HTML into text. Recommended separate sanitized HTML/plain-text storage, validated feed-body use, publisher selectors, parser comparisons, selective rendering, host cooldowns, and staged archive work in the existing portal.
- Created `news_reader_research.md` with pinned primary-source references and a phased follow-up. Updated `news_processing.md` to remove news API channels, correct stale implementation wording, and mark the new refinements as planned.
- Verification: checked saved documents, code-reference URLs against inspected repository trees, and collection-constraint wording. No comparative reader benchmark was run; no numerical extraction improvement is claimed. Only documentation changed; the running app and database were not modified by this research task.

### Luna browser assessment — 6 October 2026

- Used the OpenAI Docs skill and fetched current official documentation for GPT-6 Luna, web search, ChatGPT browsers, API computer use, pricing, and subscription plan usage.
- Verified Luna supports web search and computer-use tools. Distinguished search context, ChatGPT's desktop/cloud browser, hosted API browser sessions, and an app-provided local Playwright runtime. Account-specific hosted-browser availability was not tested.
- Assessed full-text fidelity, DOM/source/reader-mode access, JavaScript rendering, genuine missing server-side bodies, and authenticated access. No browser or model can guarantee complete text from every website; a generated answer is not proof of exact extraction.
- Recommended evaluating Luna for identifying article DOM regions or repairing publisher rules while code preserves the actual HTML/text. Routine feed/public-page extraction remains the primary approach.
- Calculated illustrative Standard API costs using 5,000 input and 1,500 output tokens/article: $1.25/1,000 articles without search, $11.25 with one billable search/article, and $21.25 with two. Extra browser iterations, reasoning, images, retries, and environment costs are excluded. Subscription usage shares existing allowances and is a separate route.
- Saved the assessment, caveats, costs, and a proposed 30–50-page comparison in section 7 of `news_reader_research.md`; referenced it as under evaluation in `news_processing.md`.
- No paid model call, browser experiment, extraction adapter, or runtime change was made. Documentation checks passed; cost figures are arithmetic estimates, not measured results.

### Final extraction remediation plan — 6 October 2026

- Consolidated the reader research and Luna assessment into the final proposed Phase 2 fix plan in `news_processing.md`. The user requested the implementation set; runtime implementation was not started in this turn.
- Ordered the work: preserve rich feed/page bodies and separate availability metrics; protect recent work from archive inventory; add validated publisher rules and better parser selection; improve cache/change detection; apply selective Chrome recovery and publisher-level cooldowns; verify completeness and throughput.
- Retained all-section collection, existing article/channel identities and history, editorial/media distinctions, attributed images, and the no-official-news-API/no-licensed-content constraint.
- Kept Luna-assisted reusable rule repair as an optional measured final step. Exact text/HTML copying remains code-driven; an additional parser adapter is conditional on measured recovery.
- Defined recent per-publisher coverage tables, separate fetch/body outcomes, 5–10-item publisher validation, and operational queue acceptance criteria. No unmeasured full-text success percentage or universal paywall access is promised.
- Verified the saved plan's structure, implementation status, and preserved constraints. Only planning/progress documents changed.

### Free extraction and reusable recipe constraint — 6 October 2026

- User clarified that routine extraction must have no paid mechanisms. Superseded the earlier per-article model/browser experiment with occasional Luna/Terra/Sol diagnosis and reusable publisher/template recipe maintenance only.
- Verified official OpenAI pricing and search-action documentation: hosted API web search has a $10/1,000-call charge plus content tokens. This is an optional API tool fee, separate from local HTTP/Chrome fetching; previous costs were hypothetical comparisons, not app expenditure.
- Updated `news_processing.md` and `news_reader_research.md` to exclude paid search, scraping services, proxies, hosted browsers, and per-article LLM extraction. Preserved the exclusion of official news APIs/licensed integrations.
- Specified multiple versioned recipes per website, template matching, selectors, rendering/wait steps, completeness checks, grouped template-failure diagnosis, fixtures plus unseen URL validation, rollback, and bounded reprocessing. Routine code retains exact source HTML/text; models propose recipes only.
- Recorded that >90% overall full-body coverage is an unverified target. Separate all-item coverage from extraction success among independently verified accessible bodies; restricted/blocked items remain in overall coverage. Retain pending, video-only, and archive reporting.
- Added an illustrative accessibility ceiling, not a forecast: 85% accessible × 97% extracted = 82.45% overall. At 97% extraction success, accessibility must exceed about 92.8% to exceed 90% overall. No comparative benchmark has been run.
- Verification: reread revised sections and checked Markdown fences and superseded per-article experiment wording. Only planning/research/progress documents changed; no runtime code, service, database, model requests, or paid extraction calls were changed or executed.

### Recent extraction window and Chrome clarification — 6 October 2026

- Added item 15 to `news_processing.md`: automatically extract all discovered articles published within the last 24 hours; historical/undated extraction requires the **Extract old articles** button. The archive job covers all stored older items across sources, independent of filters/pagination, with persistent progress, controls, duplicate-job prevention, and a worker budget that preserves recent priority.
- Specified publisher publication time as the rolling-window basis; unknown dates are not treated as recent merely because an item was newly discovered. Historical extraction claims/retries require an active manual archive job. Feed/sitemap discovery continues; already running requests may finish.
- Aligned the existing queue recommendations and `news_reader_research.md` with manual historical extraction. Current workers still process older inventory automatically; the new behavior is planned, not yet implemented.
- Inspected `newsjockey/extraction.py`: optional rendering uses Playwright to launch `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` when installed, headlessly, in a fresh temporary profile/context. It does not attach to the user's Chrome session or saved cookies, and closes the process after the request. Normal Chrome need not be open. Edge/Playwright Chromium are fallback executables if Chrome is unavailable.
- Read-only database inspection found browser fallback disabled for all 55 publishers and no current article extraction methods containing `browser`. Chrome is installed. No browser process was launched by this check.
- Verified the app process is listening on `127.0.0.1:8765`, runs `app.py` from this project with parent PID 1, and the portal root returns HTTP 200. The aggregate `/api/processing` read timed out at five seconds; no service restart or runtime change was made.
- Only the plan, research recommendations, and progress log were edited; no new extraction job, renderer, or app code was executed or changed.

### Calendar-date groups and Chrome job buttons — 6 October 2026

- User replaced the rolling 24-hour window with publication dates **today + yesterday**, with older items defined as dates before yesterday. Applied the same groups to ordinary and Chrome fallback extraction; configured portal timezone defaults to Asia/Calcutta. On 6 October, recent dates are 5–6 October and older dates are 4 October or earlier.
- Revised item 15 and added item 16 to `news_processing.md`. Specified two manually requested browser jobs: **Chrome fallback: Today + yesterday** and **Chrome fallback: Older articles**. Kept **Extract old articles** as the separate ordinary extraction button.
- Chrome jobs span all publishers in their date group, independent of table filters, with persistent progress/controls, duplicate-job prevention, restart recovery, bounded browser workers, and per-item extraction exclusion. They do not permanently enable publisher browser settings or implicitly add Chrome to ordinary jobs.
- Kept undated items in manual older jobs with separate counts; use publisher dates converted to portal timezone and refresh the automatic date group at midnight. Recent ordinary extraction remains higher priority.
- Updated research recommendations and current progress summary; preserved earlier rolling-window decisions in the historical log as superseded context. Verified active plan wording and Markdown fences. Only documentation changed; no runtime extraction scheduling or browser settings were changed.

### Updated Phase 2 execution — 6 October 2026

- User authorized implementation through Phase 2 with the revised extraction mechanisms. Baseline verification: all 51 existing tests pass.
- Created online SQLite backup `data/backups/before-extraction-upgrade-20261006-173843.sqlite3` and saved ID/count checkpoints in `data/backups/extraction-upgrade-baseline.json`: 90,331 article IDs, 376 source IDs, 55 publisher IDs, and 101,335 saved versions at backup time. No IDs will be deleted.
- Stopping the old background worker during the migration and queue replacement so historical requests cannot continue under the previous policy. The service will be restored with the updated scheduler after verification.
- Implementing free local extraction only. Normal jobs cannot launch Chrome; browser jobs require the explicit recent/older buttons. No model or paid extraction request is being made.
- Added additive schema changes for original feed HTML, sanitized body HTML, body origin/quality, fetch outcome, recipes, feed comparison policies, local caches, leases, persistent extraction jobs, and per-host cooldowns. Legacy text/IDs are retained and legacy completeness is marked unknown until revalidated.
- Implemented local candidate comparison (publisher CSS/XPath, Trafilatura HTML, Readability, body roots, public structured text), feed/page comparisons, conditional page cache, and separate recent/manual/archive/browser workers. Recipe candidates exist for all 55 current publishers plus live-blog templates; candidate presence is not measured coverage.
- Added the three requested extraction buttons, calendar-date/body-quality filters, publisher body-availability table, persistent job progress/controls, rich article rendering, and recipe diagnostic/edit/validation/rollback screens. Normal extraction cannot launch Chrome through an old publisher toggle.
- First regression run preserved 47 existing checks; four assertions reflected superseded automatic archive/per-publisher browser behavior. Updating those checks to the authorized policy and adding focused scheduling/job/content/recipe regressions before activating the service.
- Live migration completed in 10.46 seconds; all 90,331 article IDs, 376 source IDs, 55 publisher IDs, and 101,335 versions remain. SQLite quick-check passed with zero foreign-key errors. Recent queue selection measured 0.0268 seconds before further indexing.
- All 66 updated automated tests pass, including date-boundary/DST behavior, global archive jobs, pause/resume/cancel, exclusive article leases, explicit browser jobs, HTML sanitization, distinct feed comparisons, conditional cache reuse, recipe holdout validation/rollback, and retained good bodies after failures.
- Investigated slow service startup: background workers were repeatedly scanning legacy large rows for lease recovery. Added a covering partial lease index, removed redundant startup recovery scans, and added a covering availability index so extraction statistics do not read stored article bodies. Retesting service readiness and live UI.

### Verification and activation — 6–7 October 2026

- Added public English/default VTT/SRT caption retrieval (one bounded track per item), separate transcript availability, failure isolation, and attempt-count/timing tables. Publisher media files are not downloaded or transcribed.
- Live audit exposed metadata subscription flags on pages delivering a complete visible body. Retain that visible HTML and record the publisher access marker separately; actual restriction/challenge pages and hidden restricted structured bodies remain gaps. Added regressions for that distinction and UTF-8 punctuation in pages lacking charset metadata.
- Added `tools/audit_extraction.py` for five-to-ten recent samples per publisher, retained raw examples and start/middle/end paragraph anchors, structural results, failed-host deferral, and cache-only reruns. It never launches Chrome or calls a model. The initial cohort is not a random or independently rated benchmark.
- Initial audit: 171 structural complete results; after access-marker correction: 203 on the same 264-item cohort. Detailed publisher URLs/outcomes/anchors are in `artifacts/extraction-audit-20261006/audit.json`; initial results retained separately.
- Reviewed representative ABC News, Deadline, 9to5Mac, Billboard, and Verge bodies against returned source paragraphs. Found an omitted short ABC paragraph, an outdated 9to5Mac selector, and affiliate footer clutter. Activated version 2 recipes for ABC's current hostname/layout and 9to5Mac/its feed-linked 9to5Google layout, each with two development IDs and three different validation IDs. Other seed recipes remain candidates, not universally validated. The validation gate caught the sister-domain mismatch before the final 9to5 recipe passed.
- Recent workers now rotate eligible hosts and avoid simultaneous recent requests to one host. Persistent manual jobs retain separate budgets; normal jobs cannot launch Chrome through obsolete publisher toggles.
- Fixed database contention: parse feeds before acquiring the SQLite writer lock and commit inventory writes in batches of 25. Added covering lease/availability indexes, moved historical exclusion scans behind a one-time migration marker, and use normal macOS service disk scheduling (`ProcessType=Standard`). Repeated initialization measured 0.002 seconds; final recent coverage API measured 0.011 seconds. Earlier slow startups/database-lock results are retained in the historical logs.
- Final tests: all 71 pass, JS syntax and launcher shell syntax pass. Isolated UI verified all three global buttons, two-item recent versus older/undated jobs, pause/resume/cancel, archive filtering, rich headings/lists/tables/figures/attribution, and recipe screens; no console errors on the verified portal. No archive or Chrome job was started against the production database during QA.
- Real Chrome smoke test used an explicit recent job on a separate database: a 9to5Mac article saved 1,572 characters with `body_origin=browser`, `body_quality=full`, and `fetch_outcome=browser_success`. Result: `artifacts/chrome-smoke/result.json`. Existing personal Chrome profiles/tabs/cookies were not attached.
- Final ID comparison against the pre-upgrade backup: all 90,331 original article IDs, 376 source IDs, 55 publisher IDs, and 101,335 saved-version IDs remain. At verification there were 91,165 articles and 105,381 versions after ongoing collection/processing. SQLite quick-check returned `ok`; foreign-key errors: zero. Report: `artifacts/portal-qa/data-verification.json`.
- Confirmed midnight rollover live: settings and recent coverage changed automatically to 6 + 7 October in Asia/Calcutta, without service restart. Attempt telemetry at checkpoint: 77 HTTP attempts, average 5.79 seconds; these are operational attempts, not a completeness benchmark.
- Restored the main background service and stopped both temporary servers; no active manual jobs exist in production. Saved screenshot `artifacts/portal-qa/extraction-controls.jpg`. Updated README, project brief, processing specification, research status, and this progress log. No Git commits or remote changes made.


### Extraction inventory report — 7 October 2026

- User requested button location, actual extraction outcomes/reasons, and earliest dates behind pending inventory and saved versions. Verified the live older-HTTP button at the top of Article extraction; no archive/Chrome job was started.
- Read-only snapshot at 7 October 2026, 00:59:58 IST: 96,019 visible candidates, 92,920 pending, 115,565 saved versions. Pending comprises 2,352 recent, 14,051 older dated, 76,517 undated. Most undated records are sitemap-discovered URL stubs; their actual publication dates remain unknown.
- Since detailed telemetry began at 6 October 2026, 23:47:43 IST: 3,223 attempts across 2,864 IDs; 2,914 full-labelled, 59 partial, 38 blocked, 52 failed, 70 restricted, 90 media-only. Latest result per ID: 2,644 full-labelled, 40 partial, 23 blocked, 7 failed, 61 restricted, 89 media-only. All recorded production attempts ordinary HTTP/feed/cache; zero Chrome.
- Failures include repeated 404s (23 attempts / two URLs), Wired's local 4 MB fetch cap (13 / one URL), CBS Sports 406s, Ars 405s, CNET redirect cap; access failures include 401/403 and publisher restrictions. No blanket 90% claim is justified.
- Found full-body false positives: 15 of 35 full-labelled Seeking Alpha items begin with an author-bio marker; three inspected items contain biography/disclosures rather than article text. Needs follow-up classifier/recipe and revalidation correction; no production article state was changed during this analysis.
- Version counts: 96,439 initial snapshots + 19,126 subsequent copies, including metadata/status changes. ABC sample has 86 versions, with repeated feed tag/label changes rather than corresponding article-body edits. Earliest known stored article publication: 26 January 2019; first collection: 2 October 2026, 03:19:56 IST; first version save: 6 October 2026, 10:40:29 IST. Undated entries may be older.
- Saved detailed report and supporting JSON in artifacts/extraction-report-20261007/. Application code/settings/jobs unchanged; routine workers continue.

### Phases 3–5 execution — 7 October 2026

- Authorized local/free processing plus existing ChatGPT subscription inference: Luna preferred, Terra explicitly selectable; no paid API providers or automatic model escalation. Separate review screens for metadata, stories and topics; Phase 6 remains a clearly marked future screen.
- Created online backup `data/backups/before-phase-3-5-20261007-101919.sqlite3` (154,476 articles, 190,853 versions, 376 sources, 55 publishers). Existing 71 tests passed before changes.
- Verified ChatGPT CLI login and a structured Luna response. Installed NumPy locally and downloaded official Ollama 0.40.0 into the project because installed 0.30.10 cannot load EmbeddingGemma 2. Global Ollama was not changed. Local model server binds to 127.0.0.1:11435 with cloud disabled.
- EmbeddingGemma 2 downloaded successfully; first cold embedding produced a normalized 512-dimensional vector in 7.72 seconds. Model digest and operation timings are recorded. Vendor benchmarks do not establish news grouping quality.
- Added initial additive storage, persistent processing jobs, evidence-linked metadata, conservative anchor-based story candidates, topic memberships and correction APIs. Adding UI, evaluation fixtures and regression tests before activation. Suspect author-biography bodies are flagged and excluded from automatic matching.
- Added five top-level phase tabs, metadata evidence/correction dialogs, persistent batch controls, story comparisons/merge/split/reassignment, topic scope/rename/pin/merge/split/membership controls, and event-time timelines. UI checks on an isolated fixture database verified metadata edits and story split/merge; no production manual group edits were made.
- 92 automated tests pass. Structured Luna metadata and six ambiguous pair decisions succeeded with tools disabled. All 128/256/512/768 requested embedding dimensions were verified against the local runtime. Runtime reports MLX, nvfp4, approximately 1,210 MiB model residency; this is not peak process RAM.
- Fixed labeled seed evaluation: 21 synthetic items, 210 pairs. Local conservative story matching: 6 true merges, 0 false merges, 3 missed same-event pairs; local topic descriptor matching: 10 true links, 0 false links, 10 missed links. These intentionally small fixtures do not establish production precision. Embeddings retrieved paraphrases but did not independently authorize additional merges. Evaluation artifacts include per-pair decisions and six Luna checks.
- Started a bounded 23-item real-news cohort using Luna and local embeddings across all five source categories, including Jaguar and Nobel coverage. Initial contention paused one item while recording telemetry; the job retained completed work and was resumed after moving feed/page comparison outside the SQLite writer transaction.
- Live diagnostics also identified the raw-article table repeatedly sorting body-bearing rows because of an expression in ORDER BY. Replaced it with index-compatible NULLS LAST ordering (50-row lookup measured 0.002 seconds) and suppressed background raw-table polling on other phase tabs. Final activation/verification remains in progress.

- Removed a publisher-wide canonical-URL scan from the extraction write transaction, added a covering canonical index, and verified exact indexed lookups (~0.0001 seconds). This resolved the observed processing/collector writer contention.
- Live cohort classified all 23 articles; 22 were eligible for initial grouping, with one source update correctly marking its analysis stale. Initial groups include cross-publisher Nobel coverage and a syndicated MLB schedule. Topic processing is running.
- Real Jaguar coverage exposed background-year false conflicts (2024 rebrand versus 2027 deliveries). Matching now uses headline qualifiers/evidenced event dates for hard year guards and normalizes common location aliases. Added long-article chunk-date and empty-story reassignment regressions. All 97 tests pass; repeated fixed local evaluation remains 6 true / 0 false merges / 3 missed positive pairs.
- Isolated UI verification covered metadata edits, story split/merge, and topic rename/pin/split/merge; merged topic retained both dated story memberships. Production metadata/story tabs render populated tables without console errors.
- Backup-ID verification preserved all 154,476 articles, 190,853 saved versions, 376 sources and 55 publishers. Current checkpoint: 159,414 articles / 197,506 versions; SQLite quick-check `ok`, zero foreign-key errors. Report: `artifacts/phase-3-5/data-verification.json`.

- Final initial cohort: all requested runs completed. Five selected metadata records were rechecked after evidence validation refinements; Billboard now has its supported entertainment category. One continuously changing Nobel preview remains explicitly stale, rather than publishing old analysis as current.
- Added source-quote normalization for smart quotes, whitespace and inline-link punctuation spacing while preserving original text offsets; paraphrases and changed identifiers remain rejected. This corrected conservative false rejections without weakening event/entity/date checks. Exact input hashes now reconcile feed-only changes before scheduling, avoiding unnecessary subscription calls for unchanged full bodies.
- Final regression suite: 99 tests pass. The app-owned detached embedding runtime is healthy on port 11435; both subscription sign-in and the local model were checked through the portal. All six checked portal/adapter endpoints returned HTTP 200 (analysis endpoints ~0.006–0.1 seconds). No manual archive or Chrome extraction job was started.

- Phases 3–5 implementation and bounded live verification complete. Final sample: 23 analyzed records, 22 current / one changed-and-stale; 18 populated stories and 31 topic seeds. Jaguar's three reports, Nobel's two reports and syndicated MLB coverage group as expected. Topic seeds currently contain single events in this small live sample; real longitudinal quality is not established.
- All ten live verification jobs complete; 99 tests pass. Corrected the review counter to exclude resolved memberships/merged aliases. Saved model assessment, complete verification report, usage totals, source hashes, screenshot and raw results under `artifacts/phase-3-5/`. Updated specification, README and project brief. Stopped the isolated QA server and retained the main portal plus app-owned local embedding runtime. No commits or remote changes made.

### Calendar-month extraction and October story rebuild — 7 October 2026 (in progress)

- User authorized replacing daily windows with calendar months, extracting all stored October articles, extracting previous months on demand, clearing experimental derived results, and prioritizing same-event stories before topics.
- Preserved an online database backup at `data/backups/before-october-stories-20261007-182601.sqlite3`. Raw articles, extracted content, versions, channels and publishers will remain intact.
- Initial inventory: approximately 22,235 October-dated eligible articles, 7,432 structurally full bodies, and 21,541 with some text (UTC-month inventory; final counts use portal timezone). Undated sitemap inventory is excluded from dated months.
- Implemented timezone-aware calendar-month bounds, selected-month HTTP/optional Chrome controls, all-previous-month controls and a separate unknown-date scope. Normal extraction now targets the current calendar month. Explicit jobs reuse already complete saved bodies and respect host leases/cooldowns.
- Installed an isolated Python 3.12 environment with local GLiNER2 2.0 and the free `fastino/gliner2-base-v1` model. Verified real entity inference; evaluating batching before the October run. No hosted inference or subscription calls used in this rebuild so far.
- Replacing the earlier 23-item experiment with full-month durable processing. Story matching will use titles/leads, local entities and embeddings, with quality inspection; no target reduction ratio will be forced. Topics are deferred.
- Reset verification: 48 experimental article analyses, 20 story rows and 31 topic seeds cleared; all 165,431 raw articles, 207,091 versions, 376 channels and 55 publishers at reset were preserved. Full-October extraction job snapshots 22,506 IDs; processing snapshot contains 22,572 IDs collected by 18:42 IST.
- Whole-month progress, month selection, group-size filtering, pagination and deferred-topic message are live. Regression suite expanded to 107 passing checks, including calendar boundaries, headline-only input, cross-batch retrieval, cohesion and raw-data preservation.
- Throughput profiling found extraction candidate scans held SQLite's writer lock too long. Moved scans outside the reservation transaction and made host exclusivity atomic in the lease update. Measured feature batches dropped from 5–16 seconds (mostly waiting) to roughly 2–5 seconds.
- Interim read-only clustering audit (not published as the October result) found false merges among different daily puzzles, local score roundups, shopping products and successive event stages. Adding explicit event-discriminator guards and calibrating against these errors before publication. Whole-month metadata continues independently.

- 2026-10-07T14:05:46+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 10,240 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- Final activation checks: 115 regression tests pass, including a complete 505-record local worker run with hosted inference explicitly forbidden, calendar/timezone boundaries, reusable complete bodies, source-name differences, event conflicts and cross-batch clustering. Browser verification confirms selected-month/all-previous-month HTTP and optional Chrome controls, live processing progress and separate phase screens.
- Audited a bounded set of 60 real candidate pairs with subscription Luna (tools disabled; 40,156 input and 2,037 output tokens; no paid API). This is a development sample, not an independent accuracy estimate. Added event guards for different funds, products, daily editions, locations and sports developments. GLiNER2 pair classification performed poorly in a diagnostic experiment and was not deployed; it remains a metadata model only.
- Larger read-only audit covered 9,936 already processed inputs; this interim subset was not published as the October result. Found entity-boundary/missing-entity recall failures. Matching now recognizes an extracted name evidenced in both headlines and very strong paraphrases with a shared participant in the lead. Whole-month clustering still waits for every snapshot input; no forced reduction ratio and no topic generation.
- Data-preservation verification after activation: zero missing pre-reset IDs in articles, article_versions, sources or publishers; SQLite quick_check=ok, no foreign-key errors. Evidence: artifacts/october-stories/data-verification.json. Backup remains data/backups/before-october-stories-20261007-182601.sqlite3.
- Whole-month worker uses checkpointed features and resumes safely after loading matching refinements. It appends progress every 1,024 records and a completion report to this log. New articles arriving after the 22,572-record snapshot can be included by rerunning October; unchanged metadata/vectors are reused. Full extraction and feature processing are still running, so the story-quality milestone is not yet declared complete.

- 2026-10-07T14:11:35+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 11,264 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:13:22+00:00 — Delivery checkpoint: 12,016/22,572 October inputs processed; extraction job 9,551/22,506 finished (including 7,573 reused full bodies), 12,953 awaiting work. October inventory currently has 10,578 structurally full-labelled bodies. Both jobs remain active. Final 115 tests pass; browser showed no console errors, month controls and deferred-topic screen verified. Snapshot: artifacts/october-stories/current-status.json; UI proof: calendar-month-controls.png.

- 2026-10-07T14:14:14+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 12,288 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:17:13+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 13,312 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:20:40+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 14,336 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:23:27+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 15,360 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:27:02+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 16,384 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:29:50+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 17,408 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:32:03+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 18,432 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:34:37+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 19,456 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:36:59+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 20,480 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:39:35+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 21,504 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:43:06+00:00 — October/month job cc61348d89b84b2bba4ffd0f88be0e9a: 22,528 article metadata and vector records checkpointed; whole-month matching follows feature completion.

- 2026-10-07T14:45:59+00:00 — Month job cc61348d89b84b2bba4ffd0f88be0e9a finished: 22,549 articles, 3,626 multi-article stories, 2,031 cross-publisher stories, 11,568 singletons. Clusters remain provisional pending review; report saved in artifacts/october-stories.

### October completed-run assessment — 7 October 2026

- Read-only production assessment: corpus run complete (22,572 processed; 22,549 grouped into 3,626 multi-article groups and 11,568 singletons). Extraction jobs remain running. Identified clear false merges, missed Nobel event matches, 3,419 repeated-headline inputs and disagreement between pair/cluster decisions. Story-quality milestone remains unmet. Saved evidence and proposed ordered fixes in artifacts/october-stories/result-assessment.md and result-assessment.json. No application code, job state or settings changed.

### Duplicate extraction jobs and one-time backfill — 7 October 2026

- User authorized fixing duplicate extraction jobs first and clarified one-time historical backfill followed by steady-state operation. Story clustering, metadata models and topic processing are outside this change.
- Implemented canonical month interval/transport identity, database uniqueness, completed-job reuse with append-only additions, shared outcomes across overlapping queues, and an automatic-worker claim recheck to prevent stale candidates from refetching completed articles. HTTP/Chrome remain separate; explicit article retries bypass broad-job reuse.
- Added nine focused tests: concurrent aliases, appended IDs/paused jobs, completed/failed backfill reuse, overlapping scopes and transport isolation, month rollover, changed inputs, explicit retries, stale automatic claims, interrupted input changes and legacy reconciliation. All 124 tests pass.
- Backed up before live reconciliation: data/backups/before-job-dedup-20261007-221211.sqlite3. Activating migration and verifying the merged live queue and raw-data preservation.

- Live reconciliation completed: original October job retained with the union of 23,119 IDs; the second job marked merged and retained for audit. Zero missing queued IDs or backed-up raw article/version/source/publisher IDs; no foreign-key errors. Verification: artifacts/extraction-job-fix/verification.json.
- Large body-bearing article rows made migration metadata lookups slow. Added a compact covering queue index and indexed metadata lookups; live job-status read measured 0.15 seconds after activation. Migration is transactional and restart-safe.
- Final 126 tests pass. Browser checks clicked both Current month and named October: both reused the original job. Newly discovered IDs were appended (24,544 total at final UI verification), with one active HTTP job and one retained merged record. Completed work was not reset. Explicit retries and separate Chrome transport remain available; no historical-wide or Chrome job was started during testing.
- Final UI proof: artifacts/extraction-job-fix/merged-queue.png. Live queue snapshot: artifacts/extraction-job-fix/live-jobs.json. README and news_processing.md now distinguish one-time backfill from ongoing current-month extraction. No changes made to story grouping or NLP models.
