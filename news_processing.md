# News Jockey — Phased News Processing Plan

- **Status:** Calendar-month extraction and whole-month local story processing are active. The 23-article experiment was backed up and cleared. October is being rebuilt across all stored dated articles; topic processing is deferred until stories are assessed. Phases 6–7 remain planned. See `progress.md`.
- **Goal:** Extend the existing local portal to collect broadly, extract complete articles, group coverage into stories and topics, and present facts and competing views with evidence.
- **Environment:** Existing Flask/SQLite application running on the Mac. Preserve all current sources and articles.
- **Collection constraint:** Use RSS/Atom, sitemaps, and publisher web pages. Routine extraction uses free, local software only: no paid search, scraping services, proxies, hosted browsers, or per-article LLM calls; no official news APIs or licensed-content integrations. Optional subscription Luna/Terra can diagnose website templates. The 7 October authorization also permits Luna/Terra for explicitly requested Phase 3–5 processing batches; no Sol/Astra or paid API providers. See `news_reader_research.md` for inspected open-source approaches.

## 1. Overall pipeline

```text
Publishers
    |
    v
Feeds / Sitemaps / Public pages
    |
    v
Articles / Video items -> Text / Metadata / Images / Media
    |
    v
News categories / Entities / Event times / Embeddings
    |
    v
News Stories: coverage of the same event
    |
    v
Topics: related events evolving over time
    |
    v
Factual summaries / Competing views / Evidence
    |
    v
Review mode -> Existing local portal
```

- **Source category:** one primary category describing a publisher.
- **News categories:** multiple categories describing an article, story, or topic.
- **Content type:** the item's editorial function, such as reporting, editorial, opinion, analysis, or review.
- **Media formats:** the media present in an item, such as text, video, audio, or an image gallery; an item can contain multiple formats.
- **News Story:** different articles covering the same specific event.
- **Topic:** a continuing subject containing multiple related stories.
- **Publication:** making an accepted version current in the local portal.

## 2. Phase 1 — Expand publisher coverage

### Deliverables

1. Separate publishers from discovery channels. A publisher can have multiple feeds, sitemaps, or public-page inventories.
2. Expand existing publishers beyond their currently subscribed sections.
3. Collect all discoverable English publisher content, including international reporting, editorials, opinion, analysis, reviews, and video items.
4. Preserve searchable publisher suggestions and bulk URL addition.
5. Show channel coverage, polling schedules, failures, and known gaps.
6. Deduplicate overlapping channels within a publisher while preserving independent articles from different publishers.
7. Add `content_type` to every article/item: reporting, editorial, opinion, analysis, review, interview, explainer, live_blog, sponsored, other, or unknown. Preserve the publisher's original label separately.
8. Use explicit publisher/feed labels and reliable metadata first; mark uncertain types as unknown until classification or review in Phase 3. Never infer editorial function from source category alone.
9. Add `media_formats` separately from content type. Identify video and mixed text/video items from feed enclosures, Media RSS, and available publisher metadata.
10. Preserve video page/media URLs, available embed references, thumbnails, captions, duration, and publisher attribution. Keep video-only items in collection and tables even when they have no article body.

### Collection schedule

- Current scheduler checks due sources every **15 seconds**.
- Configured interval at inspection was **five minutes**, individually adjustable.
- Effective polling respects publisher TTL and failure backoff.
- Observed TTL exceptions included E! at 10 minutes, Mashable at 15, ESPN at 30, CNBC/MarketWatch at 60, and Inc. at 120.
- Retain these controls; check sitemap discovery every **30 minutes** by default.
- Collection pauses when the server stops or the Mac sleeps. Attempt recovery through available inventories after resuming.

### Completion criteria

- Source category never restricts collection.
- Existing data survives migration.
- Coverage gaps are visible rather than implying complete publisher coverage.
- An article appearing in multiple channels produces one publisher article.
- Every collected item has content-type and media-format fields. Reporting, editorial, opinion, analysis, and reviews can be distinguished and filtered.
- Video-only and mixed-media feed items are retained and visibly labeled; video is a media format, not an editorial content type.
- Without an authoritative publisher inventory, collecting every article published cannot be verified.

## 3. Phase 2 — Extract full text, metadata, images, and media

### Deliverables

```text
Article URL
    |
    v
Public HTML + Structured metadata
    |
    v
Recipes + Generic parsers -> Best body -> Explicit Chrome job
    |
    +-- Complete text
    +-- Partial text
    +-- Paywalled / Blocked / Failed / Unsupported
```

1. Fetch public article pages while retaining feed text and original HTML. Store sanitized rich HTML and plain text separately; reuse validated full-body feeds with periodic spot checks.
2. Compare publisher CSS/XPath recipes, Trafilatura, Readability, body roots, and public structured text; Chrome rendering requires an explicit job.
3. Extract body paragraphs, author, canonical URL, language, publisher dates, content-type labels, image metadata, and video/media references.
4. Record extraction status, method, attempt time, error, and content hash.
5. Preserve article versions when content changes.
6. Retry transient failures with host backoff. Automatic work covers the current calendar month; previous months and undated work require an explicit job.
7. Keep extraction independent of model processing.
8. Capture publisher-provided video captions/transcripts when available. Track transcript availability separately from article-text completeness; a video-only item without an article body is not a failed article extraction.

### Final extraction fix plan — 6 October 2026

- **Status:** Implemented through Phase 2. Candidate recipes cover current publishers; template validation and extraction audits continue. No >90% coverage claim. The optional maintenance workflow exports diagnostics and validates imported recipes; it makes no model request.
- **Scope:** Improve the existing Mac collector and portal; preserve all articles, channel identities, editorial/media labels, and history. Collect every discoverable section irrespective of source category. All routine fetching, rendering, and extraction run locally without paid mechanisms. Model-assisted recipe maintenance is the only optional exception.

```text
Feed item -> Preserve feed HTML/text -> Validated full body? -> Save
                                            |
                                            no
                                            v
                                    Fetch public article
                                            |
                           Publisher rules / Generic parsers
                                            |
                         Browser job explicitly requested?
                                            |
                              Local Chrome -> Extract again
                                            |
                         Best supported body + explicit gap

Template breaks -> Optional Luna/Terra/Sol diagnosis of examples
                -> Tested, versioned recipe -> Local extraction resumes
```

#### 2A — Preserve bodies, correct metrics, and protect recent work

1. **Keep rich content:** store original feed HTML, sanitized body HTML, and plain text separately. Preserve headings, lists, tables, links, figures, captions, and attributed images; render the HTML in existing article details.
2. **Accept validated feed bodies first:** establish full-body behavior per channel/template with page comparisons and periodic spot checks. A feed field name or length alone is insufficient. Fetch pages for missing bodies or metadata without discarding good feed content.
3. **Separate body availability from job state:** record body origin (`feed/page/browser`), quality (`full/partial/unknown/none`), fetch outcome, parser/rule version, and timestamps independently. Failed rechecks never erase a better stored body.
4. **Make stats useful:** provide publisher tables for each publication month, all previous months, unknown dates, and all dates, showing full, partial, unknown/no body, attempts, and pending jobs. Keep fetch failures and body coverage distinct; identify video-only items separately and show undated archive counts separately.
5. **Current-month priority:** automatically extract the current publication month in the configured timezone. Previous-month and unknown-date inventory stays stored and is extracted on explicit request, using a separate worker budget.

#### 2B — Improve extraction with publisher rules

6. **Add versioned publisher recipes:** support multiple recipes per website for distinct page templates (reports, opinion, reviews, live blogs, video). Match hostname, URL pattern, and DOM markers; specify CSS/XPath include/remove and metadata selectors, lazy-image handling, local rendering/wait steps, completeness checks, and verified publisher-provided public text/mobile/print variants. Seed from inspected Miniflux/FiveFilters/Mercury configurations, test current templates, and retain canonical identities.
7. **Evaluate partial results too:** keep Trafilatura and Python Readability, trying alternative candidates when rules miss or a body is incomplete. Select using paragraph retention, structure, truncation, and clutter; the longest result is not automatically best.
8. **Cache and detect changes:** retain sanitized HTML/text and versions; avoid fetching unchanged successful bodies on every poll. Bound raw-response cache size/retention and re-extract when feed fingerprints, pages, or rules change.

#### 2C — Recover browser-accessible pages and control failures

9. **Use local Chrome through explicit fallback jobs:** provide the two date-group buttons in item 16; ordinary automatic extraction and the normal archive job do not launch Chrome implicitly. Render public pages for a requested browser job, then extract the actual DOM with the same rules/parsers; preserve media/transcript distinctions and better saved bodies. The existing fallback launches the installed Chrome executable as a separate headless process with a temporary profile/context, without using the user's normal tabs, profile, or saved cookies; it closes after the request. It can launch while normal Chrome is closed. The two explicit job controls replace implicit per-publisher rendering.
10. **Handle publishers individually:** retain per-host pacing; honor rate-limit retry timing; use host cooldowns for repeated blocks, bounded timeouts, and scheduled recovery probes. Keep HTTP, browser, and archive worker budgets separate and tune concurrency from measured throughput.
11. **Explain unresolved gaps:** show the attempted route, error, retained body, publisher access marker, and next retry in article details. A restriction marker alone does not erase complete visible HTML delivered without unlocking; hidden restricted structured bodies are excluded. Distinguish blocked responses, actual restrictions, missing structure, and short/video content. Source/DOM/reader mode cannot supply a body the server never sends.

#### 2D — Validate gains; optionally use models for recipe maintenance

12. **Test the core pipeline:** start with 5–10 recent items per publisher and cover each distinct template; include short reports, long articles, reviews, live blogs, video, and known restricted cases. Compare start/middle/end paragraphs, clutter, images, and captions. Keep recipe-development examples separate from unseen validation URLs. Add meaningful regressions for discovered failures and verify safe HTML rendering. This initial audit does not establish a statistically reliable success rate for each publisher.
13. **Measure operational success:** report full-body coverage of all collected text-bearing items and extraction completeness among independently verified accessible bodies separately. Show partial/unknown/no-body and pending counts; include inaccessible articles in overall coverage, with video-only items and undated archives reported separately. Report recent volume-weighted coverage and per-publisher results, incorrect full labels, latency, and queue growth. Recent processing should keep up while the Mac is awake; manually activated archive jobs should drain within their budget.
14. **Models repair recipes only:** optionally use Luna, Terra, or Sol to diagnose representative HTML/DOM examples when a website/template breaks and propose a reusable recipe. Group repeated failures by publisher/template into one maintenance task. Code copies source HTML/text; the model never supplies missing article text. Validate the recipe against fixtures and unseen URLs before activation, keep its version and rollback path, then requeue affected items within worker budgets. Monitor template drift; use models on demand for repair, never as an automatic per-failed-article fallback. Paid search, hosted scraping/browser services, and proxies remain excluded.

#### 2E — Calendar-month extraction

15. **Automatic current month:** publisher publication date determines eligibility in the portal timezone (initially Asia/Calcutta). October 2026 means 1 October 00:00 through 1 November 00:00, end exclusive. Discovery time never establishes publication date. The automatic month changes on rollover.
16. **Explicit extraction:** a month selector plus **Extract selected month** and **Extract all previous months**. Snapshot all retained matching IDs across publishers, independent of filters/page. Reuse already complete bodies; attempt remaining articles; retain better text; expose outcomes and pause/resume/cancel. Unknown dates are a separate selectable group.
17. **Explicit browser fallback:** **Chrome fallback: Selected month** and **Chrome fallback: All previous months** use the same groups. Start an isolated temporary-profile browser only for requested jobs. No personal Chrome profile/cookies; no automatic Chrome work. Keep shared host/article exclusivity and cooldowns.

```text
Published month in portal timezone
  Current month  --> Automatic ordinary extraction
  Selected month --> [Extract selected month] / [Chrome: Selected month]
  Earlier months --> [Extract all previous months] / [Chrome: All previous months]
  Unknown date   --> Separate explicit selection; never assumed to be October
```


- A local Mozilla Readability/Mercury adapter is a benchmark candidate if the existing parsers still miss accessible bodies; it is not a prerequisite for these fixes. Research and Luna assessment: `news_reader_research.md`.

### Coverage target and confidence

- **>90% overall full-body coverage is a target, not a demonstrated result or promise.** No comparative benchmark of the proposed pipeline has run.
- High extraction completeness on publicly accessible templates is plausible; actual accessibility of the selected publishers must be measured. A recipe fixes page structure, not a full body withheld by the server.
- Ignoring queue delay: `overall full-body coverage = full-body accessibility × extraction success on accessible bodies`. Illustrative only: `85% × 97% = 82.45%`; with 97% extraction success, accessibility must exceed about 92.8% to exceed 90% overall.
- Validate against recent, representative, unseen articles; publish achieved rates, sample sizes, and failure causes. Expand the initial audit before claiming sustained >90% performance. Neither accessible-only results nor heuristic "complete" job labels establish >90% overall coverage.

### Image handling

- Collect all publisher-provided article images and video thumbnails, including images with missing or unrecorded reuse permissions.
- Extract image URLs, captions, credits, and source article/publisher links. Retain licensing information when supplied as optional metadata.
- Store every collected image candidate; missing permission records do not exclude images, prevent cover selection, or hold publication.
- Select story/topic covers from collected images using their relevance and available captions. Use a placeholder only when no usable image is available.
- Always display attribution: publisher and source link, plus photographer/agency credit and caption when supplied. Do not invent missing credits.
- Identify archival imagery where known and avoid misleading depictions or crops.

### Completion criteria

- The portal distinguishes complete text from feed excerpts and partial extraction.
- Inaccessible articles remain visible with an explanation.
- Missing article text is never generated by an LLM.
- Extraction handles malformed pages, redirects, updates, and failures without losing articles.
- Text length alone does not establish extraction completeness.
- All collected images have source attribution, including images without recorded reuse permissions.
- Video metadata and transcript availability remain distinct from article-text extraction status.

## 4. Phase 3 — Categories, processing adapters, and evaluation

### Deliverables

1. Introduce controlled, multi-label news categories.
2. Refine the content-type labels introduced in Phase 1 using classification and review. Keep content type, media formats, and news categories separate; retain publisher labels and record confidence and manual corrections.
3. Extract entities, locations, and event times with supporting passages.
4. Add interchangeable adapters for embeddings, structured decisions, and generation.
5. Record model/provider versions, prompts, latency, token usage, memory, and estimated cost.
6. Create a labeled evaluation dataset before selecting the production configuration.

### Initial news categories

- Politics & Government
- Business & Economy
- Finance & Markets
- Technology
- Science
- Health
- Entertainment & Culture
- Sports
- World & Diplomacy
- Crime & Justice
- Climate & Environment
- Weather & Disasters
- Education
- Labor
- Housing
- Transportation
- Lifestyle
- Religion

- Track National/State/Local/International separately as geographic scope.
- Dedicated science, health, climate, local/regional, education, justice, and international sources are coverage expansion priorities.

### Active implementation — October corpus rebuild

1. **Scope:** all retained non-duplicate, non-excluded articles published in the selected calendar month; title-only items included. Raw records and versions preserved. The earlier experimental metadata/stories/topics were backed up and cleared.
2. **Input:** normalize title and up to 1,100 lead characters from saved article/feed/transcript text. Reject known biography-only bodies. Save exact input, source hash and processing version; full text remains stored separately for later evidence and summaries.
3. **Metadata:** free local **GLiNER2 base v1 (205M)**, simultaneous multi-label news classification and people/organization/location/product extraction from title + the first 500 lead characters (embedding input retains up to 1,100). Entity names require exact input evidence; classifier scores are not calibrated probabilities. Publisher content labels remain authoritative when present. Event time stays unknown pending evidence review.
4. **Representations:** local **EmbeddingGemma 2**, separate normalized **512-dimensional** title and title+lead vectors. Save model digest; do not mean-pool entire articles into a diluted event description.
5. **Retrieval:** NumPy cosine search across the entire month, top 32 neighbors per article. Memory-bounded calculation batches do not constrain candidate membership. Score = 55% title similarity + 45% lead similarity.
6. **Same-event decision:** combine semantic scores, IDF-weighted headline overlap, entity overlap (including names evidenced in both headlines despite NER boundary differences), time proximity and explicit quarter/game/date conflicts. Shared people or subject matter alone do not establish the same event. Delayed/ambiguous matches remain review candidates.
7. **Clustering:** process strongest accepted pairs; every incoming member must match a stable representative, with an additional distant-member consistency check. Keep singletons when evidence is inadequate. Never force an article-to-story reduction ratio.
8. **Review:** separate counts for multi-article stories, cross-publisher stories and singletons. Paginated searchable tables, input/evidence inspection, merge/split/reassignment. Representative publisher headline only at this stage.
9. **Operations:** persistent whole-month jobs without the old 25/500-item cap; pause/resume/cancel, model versions, checksums and checkpoints. New arrivals enter a subsequent month run; unchanged features are reused, manual corrections retained.
10. **Costs and later phases:** no subscription/paid model in this bulk pipeline. Luna, then explicit Terra, may be used for bounded diagnosis or ambiguous-case auditing. Topics and factual generated outputs wait for story-quality assessment. No generated topic seeds during this rebuild.

```text
All stored October articles (title + available text)
          |                              |
          |                      Full-text extraction continues
          v
   Saved title + lead inputs
          |
   GLiNER2 categories + NER
          |
   EmbeddingGemma 2: title vector + lead vector
          |
   Search neighbors across ALL October inputs
          |
   Same-event checks + cluster consistency
          |
   Multi-article stories | singletons | review candidates
          |
   Evaluate missed matches and false merges
          |
   Topics / factual outputs (later milestone)
```

- Validation must include real cross-publisher examples, recurring sports/earnings events, and both accepted and rejected matches. Synthetic regressions and model-vendor benchmarks do not establish production quality.
- Earlier experimental artifacts remain in `artifacts/phase-3-5/`; October rebuild artifacts are in `artifacts/october-stories/`. Earlier quality counts do not describe the new pipeline.

### Historical processing candidates (not all enabled)

| Approach | Candidates | Role and expected limitation |
|---|---|---|
| Classical methods | URL/hash, MinHash, BM25, TF-IDF, entity/date rules | Cheap baseline; misses paraphrases and can confuse recurring events |
| Local embeddings | MiniLM, BGE-base, E5-base, Nomic, EmbeddingGemma 300M, Qwen embeddings | Semantic retrieval; similarity alone does not establish the same event |
| Hosted embeddings | OpenAI small/large | Convenient retrieval benchmark; metered input |
| Structured decision models | Jev, Kev, Laya, CLM, Julia-1 | Matching, category, and stance decisions; do not replace prose generation |
| Local generative models | Qwen3-8B-GGUF and suitable alternatives | Extraction and summaries; benchmark memory, speed, and factuality |
| Hosted generation | DeepSeek, GLM, GPT-6 Luna, Ling, stronger comparison models | Verification and synthesis; quality must be measured |
| Subscription adapters | Supported Codex/Claude Code and other CLI interfaces | Shared quotas and provider terms; harness choice does not determine model quality |

- Current selection supersedes the original Qwen proposal: SQLite lexical retrieval plus **EmbeddingGemma 2**. Other models remain possible future comparisons.
- The current M2 Pro/16 GB Mac favors small local models; benchmark quantized 8B generation separately.
- These are not all 8B models: Julia-1 is approximately 144M parameters; Kev has several sizes. CLM uses a Qwen3-8B encoder.
- Julia-1 ONNX is an alternate runtime, not a separate quality tier. CLM's documented deployment does not establish a ready-to-run Mac configuration.
- Respect embedding input limits and formatting; chunk long articles instead of silently truncating them.
- Use supported authentication. Do not treat ChatGPT OAuth tokens as generic API credentials.
- Treat article content as data; inference adapters must not expose shell or file-writing tools to it.
- Paperclip provides adapter examples; installing it is not required. Pi, OpenCode, Hermes, and similar harnesses still depend on their selected model and billing route.

### Historical cost comparison — paid routes disabled

Illustrative workload: **4 million input and 0.4 million output tokens per 1,000 articles**, including amortized story/topic processing.

| Model/provider route | Approximate generation cost per 1,000 articles |
|---|---:|
| Ling 3 Flash / Novita | $0.109 |
| Ling 3 Flash VL / Novita | $0.109 |
| DeepSeek V4 Flash 0731 / Sail Research | $0.196 |
| GPT-6 Luna batch / OpenAI | $0.300 |
| GLM-5.3 Flash / Relace | $0.340 |
| DeepSeek V4.1 Flash / Morph | $0.385 |
| GPT-6.1 Sol | $12 |
| Claude Opus 4.7 | $30 |
| GPT-6 Astra | $60 |

- Historical pricing snapshot: **2 October 2026**. Refresh rates before benchmarking.
- These estimates exclude extraction, retries, additional reasoning, images, and repeated rewrites.
- At 10,000 articles/day, multiply by approximately 300 for a 30-day month.
- Local processing has no token bill but consumes hardware, electricity, and time.
- Subscriptions may have no incremental charge within included allowances; throughput and quotas remain constraints.
- OpenAI embedding prices in the researched snapshot were $0.02/$0.13 per million input tokens for small/large. Jev advertised $0.042 per million input tokens.
- Verify batch delivery behavior before using it for freshness-sensitive work.
- Vision capabilities may help assess image relevance when captions and metadata are insufficient.

### Completion criteria

- Every processing run has reproducible configuration and usage records.
- Benchmarks compare cost, quality, and latency on the same labeled examples.
- No model is declared best from price or vendor benchmarks alone.
- Expected strengths are hypotheses; numerical quality claims require News Jockey evaluation.

## 5. Phase 4 — Group articles into News Stories

### Deliverables

1. Retrieve the top 20 candidate stories using lexical search, embeddings, entities, and time.
2. Prioritize a 72-hour window, while allowing older matches for delayed reporting.
3. Verify the event's participants, action, date, location, and distinguishing details before merging.
4. Prevent chains of loosely similar articles from joining unrelated events.
5. Identify syndicated coverage and distinguish publisher count from independent reporting count.
6. Add story list/detail interfaces and manual merge, split, and reassignment controls.
7. Give ordinary articles one primary story; roundups can reference several without merging them.

### Examples

- Reports about the same court ruling → one story.
- A subsequent appeal → a separate story.
- Earnings reports for different quarters → separate stories.
- Delayed reporting about the original ruling → potentially the original story.

### Completion criteria

- Same-event coverage groups correctly across publishers.
- Related but distinct events stay separate.
- Ambiguous matches remain separate or enter review.
- Automatic merge precision target: **at least 98%**, measured on held-out real examples. **Not yet established**; current model confidence thresholds do not equal measured precision.

## 6. Phase 5 — Organize evolving Topics

### Deliverables

```text
Topic: Merger review
    |
    +-- Announcement
    +-- Regulatory investigation
    +-- Court ruling
    +-- Appeal
```

1. Discover topics using semantic relationships, entities, and explicit topic descriptions.
2. Allow stories to belong to multiple topics.
3. Maintain persistent topic identities across months and years.
4. Avoid grouping everything mentioning one person or company into a single topic.
5. Build timelines using event times, including approximate or unknown dates.
6. Add topic list/detail interfaces.
7. Support rename, merge, split, pin, and membership correction.

### Completion criteria

- Related events form a coherent evolving subject.
- New developments can join an older topic.
- Manual corrections survive reprocessing.
- Topic timelines do not substitute publisher publication dates for unknown event dates.

## 7. Phase 6 — Generate factual outputs and competing views

### Required outputs

| Field | News Story | Topic |
|---|---|---|
| Title | Direct factual headline | Direct factual title, maximum six words |
| Summary | Target 30–40 words; maximum 40 | Maximum 100 words |
| Views | Material competing claims and positions | Overall positions and changes over time |
| Entities | People, organizations, relevant entities | Consolidated entities |
| Locations | Event locations | Relevant locations |
| Sources | Linked articles and reporting counts | Sources across stories |
| Event information | Event time, precision, evidence | Timeline of story headlines and event times |
| Platform timestamps | First published/latest published update | First published/latest published update |
| Categories | Multiple news categories | Multiple news categories |
| Cover | Relevant publisher image with attribution | Representative publisher image with attribution |

### Editorial rules

1. Ground factual claims and positions in cited article passages.
2. Distinguish facts, allegations, opinions, predictions, and unresolved disputes.
3. Use specific, direct language while preserving necessary attribution and uncertainty.
4. Organize views around explicit questions and stakeholder positions: supports, opposes, conditional, mixed, or unclear.
5. Separate a speaker's quoted position from the journalist's framing.
6. Show conflicting accounts and evidence gaps.
7. Avoid fabricated opposing views or equal evidentiary weight for unsupported claims.
8. Treat political source orientation as optional context, not article-level truth.
9. Preserve publisher dates, collector dates, event dates, and platform publication dates separately.
10. Record when positions were expressed; historical positions must not silently become current ones.

### Completion criteria

- Word limits and required fields pass automated checks.
- Every generated factual claim has an evidence reference.
- Unsupported claims and unresolved conflicts are flagged.
- Cover images have source attribution and appropriate event/topic relevance; recorded reuse permission is not a selection or publication requirement.
- Claims based on partial text show that limitation; all views means material views found in collected evidence, not a promise of exhaustive viewpoints.

## 8. Phase 7 — Review, feedback, and release

### Review modes

| Mode | Publication behavior |
|---|---|
| Review exceptions — default | Publish versions passing checks; hold flagged versions |
| Review everything | Hold every new generated version for approval |
| Automatic | Publish versions passing mandatory checks; display uncertainty |

- Schema, word-limit, evidence-reference, image availability, and attribution checks apply in every mode. Missing reuse-permission records do not hold publication.
- Failed updates retain the last accepted version with a pending-update indicator.
- Mode changes do not automatically approve the existing backlog.

### Portal additions

- **Articles:** full-text status, news categories, content type, media formats, video/transcript metadata, attributed images, evidence, and story/topic links; filter by content type and media format.
- **Sources:** discovery channels, section coverage, schedules, extraction success.
- **Stories:** sortable table and evidence/detail view.
- **Topics:** searchable table and chronological timeline.
- **Review:** grouping, categories, claims, dates, and image decisions.
- **Processing:** configuration, queues, costs, latency, and benchmark results.

### Learning loop

```text
Human corrections
       |
       v
Labeled examples
       |
       v
Threshold / Prompt / Model changes
       |
       v
Held-out evaluation
       |
       v
Versioned configuration release
```

- Record corrections as training/evaluation examples.
- Review alone does not automatically train a model.
- Preserve explicit human corrections during reprocessing.
- Choose the lowest-cost configuration meeting quality targets.
- Retain human review where no configuration meets those targets.

### Interfaces and processing behavior

- Add publisher/channel relationships, content type, media formats and references, transcript availability, extraction status, article versions, stories, topics, evidence, attributed images, review decisions, and processing runs.
- Add list/detail/update interfaces for stories and topics, membership corrections, review actions, and processing configuration.
- Preserve existing article/source identifiers and compatible API behavior during migration.
- Use persistent, resumable jobs keyed by content version and processing configuration.
- Reprocess affected stories/topics after changes; avoid repeatedly sending entire topic histories.
- Keep generated versions linked to the article versions supporting them.

### Final verification

1. Use approximately 1,000 labeled articles, 300 difficult event pairs, 100 topic links, and 200 summary claims.
2. Split evaluation data by event and time to reduce leakage.
3. Measure extraction quality, retrieval recall, grouping errors, categories, stance, claim support, latency, memory, and cost.
4. Test overlapping feeds, article updates, restarts, paywalls, partial text, roundups, delayed reporting, conflicting dates, video-only and mixed-media feed items, and unavailable images.
5. Verify migrations preserve existing records and resumable jobs avoid duplicate processing.
6. Test every review mode, preserved corrections, and story/topic merge and split operations.
7. Verify distinct reporting/editorial/opinion/analysis/review labels and unknown-type handling. Verify images without recorded reuse permissions are collected, attributed, and eligible for covers.

### Initial quality targets

- Candidate retrieval recall@20: **≥95%**
- Automatic same-event merge precision: **≥98%**
- News-category macro-F1: **≥90%**
- Generated factual claims with evidence references: **100%**
- Audited factual claims supported by evidence: **≥98%**

These are acceptance targets, not measured results or guarantees.

## 9. Reference links

- Extraction: [Trafilatura](https://trafilatura.readthedocs.io/en/latest/)
- Viewpoint methodology: [Ground News](https://ground.news/rating-system)
- Adapter examples: [Paperclip](https://github.com/paperclipai/paperclip)
- Codex: [Non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [authentication](https://learn.chatgpt.com/docs/auth)
- Claude Code: [Current usage terms](https://code.claude.com/docs/en/legal-and-compliance)
- Decision models: [Jev](https://typesafe.ai/), [Kev](https://github.com/jaredpalmer/kev), [Laya](https://github.com/NandhaKishorM/laya), [CLM](https://github.com/Contrastive-LM/CLM), [Julia-1](https://huggingface.co/SupersonicLabs/Julia-1), [Julia-1 ONNX](https://huggingface.co/SupersonicLabs/Julia-1-ONNX)
- Embeddings: [MiniLM](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), [BGE-base](https://huggingface.co/BAAI/bge-base-en-v1.5), [E5-base](https://huggingface.co/intfloat/e5-base-v2), [Nomic](https://huggingface.co/nomic-ai/nomic-embed-text-v1), [EmbeddingGemma](https://huggingface.co/google/embeddinggemma-300m), [Qwen3 Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- Local generation: [Qwen3-8B-GGUF](https://huggingface.co/Qwen/Qwen3-8B-GGUF)
- Hosted generation: [DeepSeek V4.1 Flash](https://openrouter.ai/deepseek/deepseek-v4.1-flash), [DeepSeek V4 Flash 0731](https://openrouter.ai/deepseek/deepseek-v4-flash-0731), [GLM-5.3 Flash](https://openrouter.ai/z-ai/glm-5.3-flash), [GPT-6 Luna batch](https://openrouter.ai/openai/gpt-6-luna:batch), [Ling 3 Flash](https://openrouter.ai/inclusionai/ling-3.0-flash), [Ling 3 Flash VL](https://openrouter.ai/inclusionai/ling-3.0-flash-vl)
- Hosted model pricing: [OpenRouter catalog](https://openrouter.ai/api/v1/models)

## Extraction queue refinement — 7 October 2026

1. Canonical job identity uses transport and effective date bounds. Current month and the corresponding named month share one job; repeated requests append new IDs without resetting completed items.
2. Historical extraction is a one-time backfill. Share outcomes for unchanged article inputs across overlapping selections; complete, partial, restricted and failed results are retained. Intentional article retries remain explicit.
3. Routine current-month workers handle new/changed feed items with existing backoff. Calendar rollover does not restart completed history. HTTP and explicitly requested Chrome remain separate.
4. Consolidate the existing October jobs into their union, keep the older job ID, retain the second record as merged history, and preserve raw data and all saved versions.
