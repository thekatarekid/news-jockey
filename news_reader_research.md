# Open-source news reader research

- **Date:** 6 October 2026.
- **Scope:** The six readers in the screenshot, additional readers/tools, primary documentation, and extraction source code. Repository links below pin the inspected snapshots; these include development branches, not only releases.
- **Constraint:** Extend the existing Mac portal using RSS/Atom, sitemaps, and publisher web pages. Routine extraction must use free local software: no official news APIs, licensed-content integrations, paid search, scraping services, proxies, hosted browsers, or per-article model calls. Occasional model-assisted recipe diagnosis/repair is the only optional exception; paid dependencies mentioned below describe other readers, not our implementation plan.
- **Status:** Research informed the implemented Phase 2 refinements on 6 October. No reader or paid extraction service was installed. See `progress.md` and the local structural sample audit; no independently verified comparative completeness benchmark is claimed.

## 1. What they actually do

1. Read the feed's existing content, which may be a complete article or an excerpt.
2. Fetch the linked web page when more content is needed.
3. Extract its article region using publisher-specific CSS/XPath rules or a general Readability-style parser.
4. Keep cleaned HTML for reading: paragraphs, headings, links, lists, images, and captions. Plain text is a separate representation.
5. Cache content and retain feed text when page retrieval fails.
6. Some outsource extraction to a service; some expose it only when the reader requests it. A rich-text view does not prove that every publisher's complete article was obtained.

```text
RSS/Atom item
     |
     +-- Sufficient feed body ------------------------+
     |                                               |
     +-- Excerpt -> Fetch article HTML                |
                         |                           |
                 Publisher extraction rules          |
                         |                           |
                 Generic parser if needed            |
                         |                           |
                         +---------------------------+
                                                     |
                                      Sanitize + resolve URLs
                                                     |
                                      HTML + text + metadata
                                                     |
                                         Store / cache / display
```

## 2. Readers from the screenshot

| Reader | Verified extraction mechanism | What we should borrow |
|---|---|---|
| **Miniflux** | With crawling enabled, fetches new entries' pages; uses custom/predefined CSS rules or its own Go Readability implementation when no rules apply. Rewrites and sanitizes extracted HTML. Also supports manual fetching. [Scraper](https://github.com/miniflux/v2/blob/c52bdef6e9811f7c20cebc034a6c0ec6799dc6df/internal/reader/scraper/scraper.go), [processor](https://github.com/miniflux/v2/blob/c52bdef6e9811f7c20cebc034a6c0ec6799dc6df/internal/reader/processor/processor.go). | Local extraction, publisher rules, lazy-image corrections, and public URL variants. Its [rules documentation](https://miniflux.app/docs/rules.html) gives NPR's text site as an example. |
| **FreshRSS** | Full-content retrieval is configured per feed with an article CSS selector, converted to XPath. Fetches/caches HTML, removes selected clutter, sanitizes, and replaces/appends/prepends the result while retaining original feed content. [Implementation](https://github.com/FreshRSS/FreshRSS/blob/219eaf58a7a1299f69af3be994862d5fcf763127/app/Models/Entry.php), [configuration](https://freshrss.github.io/FreshRSS/en/users/04_Subscriptions.html). | Configurable extraction selectors, retained originals, cache reuse, and visible rule failures. Built-in full-content fetching needs configuration. |
| **NetNewsWire** | Reader View sends a signed request to `extract.feedbin.com`; it receives article HTML from that service. Its README says Reader View is disabled in builds without the unshared keys. [Extractor](https://github.com/Ranchero-Software/NetNewsWire/blob/8cff640ec1633713912abc376071d50f66e3bcdd/Shared/Article%20Extractor/ArticleExtractor.swift), [README](https://github.com/Ranchero-Software/NetNewsWire/blob/8cff640ec1633713912abc376071d50f66e3bcdd/README.md). | Useful reading workflow; its extraction feature depends on an external service in the inspected code. |
| **Fluent Reader** | Fetches article HTML locally, then passes that HTML to bundled Mercury Parser and displays the returned HTML. Its full-content path uses a normal fetch; loading the original webpage is a separate view. [Fetch](https://github.com/yang991178/fluent-reader/blob/58139987094f05a6f5a662070606d17635ff188e/src/components/article.tsx), [parser invocation](https://github.com/yang991178/fluent-reader/blob/58139987094f05a6f5a662070606d17635ff188e/dist/article/article.js). | Local parsing of fetched HTML. Electron alone does not mean its extraction executes publisher JavaScript. |
| **NewsBlur** | Text View first calls its Mercury-based Node service, then falls back to fetching the page and Python Readability. Retains longer extracted HTML separately from feed content. Current code also has an optional, configured ScrapingBee paid proxy fallback for blocked requests. [Text importer](https://github.com/samuelclay/NewsBlur/blob/c51f6daa586b2eed55f9218adfd2bfd6bb923e8b/apps/rss_feeds/text_importer.py), [Node extractor](https://github.com/samuelclay/NewsBlur/blob/c51f6daa586b2eed55f9218adfd2bfd6bb923e8b/node/original_text.js). | Multiple parsers and separate feed/page bodies. Its core extractor can be self-hosted; the optional proxy is a distinct dependency. |
| **Feedbin** | Rails calls a separately configured Extract service. That open-source Node service wraps Mercury; its inspected package uses `@jocmp/mercury-parser`. [Client](https://github.com/feedbin/feedbin/blob/6cc669c7a8f1abff298a92e8c9ce66230ed81067/app/models/mercury_parser.rb), [Extract](https://github.com/feedbin/extract/tree/2affa4d70657dd2f5ccaddd4260e844f1e993f3e). | A reusable local extraction worker is possible. Its service protocol is internal infrastructure, not a publisher news API. |

## 3. Additional projects and reusable parts

| Project | Finding and relevance |
|---|---|
| **Read You** | Android reader uses local Readability4J Extended to obtain both an article DOM and plain text. Confirms rich text does not require an LLM or news API. [Parser](https://github.com/Ashinch/ReadYou/blob/d2b979ccad9a3e54b9499929a9dc2824578b0fd9/app/src/main/java/me/ash/reader/infrastructure/html/Readability.kt). |
| **Wallabag / Graby** | Read-later application, not an RSS reader. Fetches pages through Graby, which combines PHP Readability with FiveFilters site configurations for bodies, metadata, cleanup, lazy images, and pagination. [Integration](https://github.com/wallabag/wallabag/blob/66314b5e1b934192f8a968bff4ae1b4f769b924e/src/Helper/ContentProxy.php), [Graby extractor](https://github.com/j0k3r/graby/blob/cb1b558c2e526f832d3736f8db2a05c5a5ce0661/src/Extractor/ContentExtractor.php). |
| **RSS-Bridge** | Feed generator/expander. Its CSS Selector Feed Expander fetches linked pages, selects article HTML, removes configured clutter, and enriches metadata. Useful for sites with weak feeds. [Bridge](https://github.com/RSS-Bridge/rss-bridge/blob/18620ee3cde75a07449871ec60cd1c1e61710e87/bridges/CssSelectorFeedExpanderBridge.php). |
| **morss** | Python full-text feed converter. Fetches item URLs, uses its own DOM-scoring extractor or supplied XPath, resolves links, and serializes article HTML. Has cache and processing limits. [Fetcher](https://github.com/pictuga/morss/blob/c5b2df754ee2c1d482a008d1cb5bf622c1aa5592/morss/morss.py), [extractor](https://github.com/pictuga/morss/blob/c5b2df754ee2c1d482a008d1cb5bf622c1aa5592/morss/readabilite.py). |
| **yarr** | Local Go/SQLite RSS reader with a Go Readability implementation. However, the inspected default branch has page downloading commented out in its crawl handler. Do not choose that branch as a working full-text reference without release testing. [Handler](https://github.com/nkanaev/yarr/blob/1f170c589c6c1dde63b174be856a70cc9ca54540/src/server/routes.go). |

- **Mozilla Readability:** returns both article HTML and plain text; its documentation explicitly separates extraction from HTML sanitization. A local Node adapter is an option. [Documentation](https://github.com/mozilla/readability/blob/ab4027a8b37669745016869a37a504727992b2ba/README.md).
- **Mercury:** the inspected [jocmp fork](https://github.com/jocmp/mercury-parser/tree/c583cae6a3cba627e323aac4b06fa59382e7f1c9) contains 207 domain extractor `index.js` files, including Ars Technica, CNBC, and The Verge. Treat these as candidate rules, not verified coverage.
- **FiveFilters:** the inspected [configuration repository](https://github.com/fivefilters/ftr-site-config/tree/02bd5fea27caaf0d609ca547ad73dab3aef525b6) contains 2,049 `.txt` configurations, including general rules. Exact hostname files exist for **29 of our current 55 publishers**, including Fox, NPR, CBS, TechCrunch, NYT, WSJ, Bloomberg, Politico, and The Verge. Some contain only cleanup instructions or old test URLs; presence does not establish current full-text access. [Rule format](https://help.fivefilters.org/full-text-rss/site-patterns.html).

## 4. What differs from News Jockey today

1. Feed collection applies `clean_text`; article extraction requests Trafilatura's text output. Formatting is lost before storage. Images/media are stored separately.
2. We already have public-page fetching, Trafilatura, Python Readability, optional Chrome rendering, persistent jobs, and snapshots.
3. We lack a publisher-specific extraction-rule layer and preserved, sanitized article HTML.
4. Readability fallback currently runs only in certain cases; a partially extracted article can avoid that fallback.
5. Queue state and body availability are different: pending or blocked page extraction can coexist with usable feed or previously saved text.
6. A parser can improve extraction from returned HTML. It cannot create article content when the response is a refusal, challenge, or excerpt-only page. Reader feature descriptions do not establish success rates for our publishers.

## 5. Recommended follow-up within Phase 2

### 2A — Preserve content and measure availability

- Store original feed HTML, sanitized body HTML, and plain text separately. Preserve headings, tables, lists, links, figures, and captions; keep media references and attribution.
- Record body origin (`feed/page/browser`), body quality (`full/partial/unknown/none`), extraction-rule/parser version, and fetch outcome independently.
- Validate full-body feed behavior per publisher/template; accept those bodies first, with periodic page comparisons. Neither `content:encoded` nor length alone proves completeness.
- Show body availability by publisher and recent publication window alongside job-status counts.

### 2B — Add publisher rules and compare parsers

- Add versioned CSS/XPath include/remove rules and lazy-image normalization to the existing Python worker.
- Seed rules from Miniflux, FiveFilters, and Mercury; validate current templates before enabling them. Preserve generic extraction when a rule misses.
- Compare rule extraction, Trafilatura, Python Readability, and optionally local Mozilla Readability/Mercury on the same returned HTML. Select by measured completeness and clutter, not longest text alone.
- Keep network fetching, URL validation, scheduling, and retries under News Jockey's existing controls.

### 2C — Render selectively and clear the backlog

- Use existing installed-Chrome rendering through two manually requested jobs: **Chrome fallback: Today + yesterday** and **Chrome fallback: Older articles**. Run the same rules/parsers on the rendered DOM; ordinary extraction jobs do not implicitly launch Chrome.
- Use verified public text/mobile/print versions where the publisher offers them; retain canonical article identity.
- Apply host-level cooldowns for rate limits and repeated blocks. Avoid repeatedly downloading unchanged failures.
- Automatically extract discovered articles published on today's or yesterday's calendar date in the configured portal timezone (default **Asia/Calcutta**). Older means before yesterday. Keep older/undated inventory for an explicit **Extract old articles** background job covering all retained historical items across publishers, independent of table filters. Use identical date groups for both Chrome buttons, count undated items separately, and keep recent ordinary work higher priority; do not automatically drain the historical queue.
- Cache successful HTML/text and preserve it through rechecks; bound raw-response cache size and retention.

## 6. Verification before claiming improvement

- Review 5–10 recent items per publisher, including short reports, long articles, reviews, live blogs, video, and restricted pages.
- Measure: page-fetch success; usable body availability from any origin; complete-body recall; clutter; images/captions retained; latency; and queue drain rate.
- Compare first/last paragraphs and missing middle sections against publisher-visible text. Evaluate short and video-only items separately.
- Test stale selectors, redirects, encodings, lazy images, malformed HTML, and script/event-handler removal.
- Report results per publisher and for recent news separately from archives. Do not promise a percentage until this sample is evaluated.

**Recommendation:** Borrow the Miniflux/FreshRSS rule approach and the HTML-plus-text storage approach. Keep the current portal and Python workers; benchmark a local Mercury or Mozilla adapter only where it improves results. No official news API or licensed-content dependency is needed for this plan.

## 7. Luna with a browser — assessment, 6 October 2026

- **Verdict, revised:** Use Luna/Terra/Sol only for occasional website/template diagnosis and reusable recipe creation or repair. All routine article extraction uses local code and an optional local browser. Browser access and exact text preservation remain the main constraints; a stronger model does not remove them.
- Official documentation lists **GPT-6 Luna** as supporting web search and computer use. These are tools enabled by a product or integration, not unrestricted website access inherent in the model. [Model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).

### Available routes

| Route | Assessment |
|---|---|
| Built-in web search | Searches and opens pages to produce sourced answers. Returned search context is controlled by tool settings; this is not a guaranteed full HTML/article export. Useful for discovery and checking, weaker as the archival ingestion path. [Documentation](https://developers.openai.com/api/docs/guides/tools-web-search). |
| ChatGPT browser | Can navigate rendered pages. Desktop Developer mode can expose DOM/network information through CDP; access depends on settings and permissions. Cloud browsing uses a separate environment, with sign-in support varying by plan/rollout. [Browser documentation](https://learn.chatgpt.com/docs/browser). |
| API agent with a browser | OpenAI documents hosted browser sessions; alternatively our app can provide a local Playwright runtime through tools. The local route lets code save the actual HTML/text directly. Luna's tool support does not establish account-specific hosted-browser availability. [Hosted browser](https://developers.openai.com/api/docs/guides/agents-api/tools/computer-use), [local integration](https://developers.openai.com/api/docs/guides/tools-computer-use). |

### Access and reliability

1. **Public HTML:** a DOM parser can copy accessible text exactly; an LLM can help identify the relevant region.
2. **Public JavaScript pages:** a browser can render content absent from the initial response, provided the site works in that browser session.
3. **Source/DOM/reader mode:** these expose or reorganize content already supplied to the browser. If full text is present, extraction may find it; this does not require an LLM.
4. **Hard paywall:** if the server sends only an excerpt, neither source inspection nor reader mode supplies the missing body. An authenticated account with access may receive more content; that is a different access state.
5. **Blocks, challenges, rate limits:** an agent can still fail or need human intervention. No universal website or paywall access is established by the documentation.
6. **Verbatim fidelity:** model-produced text can omit, reorder, or paraphrase passages. Prefer model-selected DOM regions followed by deterministic HTML/text copying, with saved provenance and completeness checks. Screenshots/OCR are less suitable for preserving exact rich text.

### What "billable web search" meant

- OpenAI's optional hosted web-search API tool has a **$10/1,000-call charge ($0.01/call)**, plus retrieved-content tokens billed at model rates. This is separate from ordinary local HTTP requests or local Chrome page visits. Search actions incur the tool-call charge; opening a page is not automatically a billable search action. [Tool pricing](https://developers.openai.com/api/docs/pricing), [search actions](https://developers.openai.com/api/docs/guides/tools-web-search).
- Earlier per-article API cost calculations were hypothetical comparisons, not News Jockey expenditure. They are superseded by the user's free-extraction constraint; the historical assessment remains recorded in `progress.md`.
- No paid search or hosted extraction route is selected. Optional model use is bounded website/template maintenance, using an available supported model/subscription integration. Subscription-backed requests still share plan limits. [Plan usage](https://learn.chatgpt.com/docs/sign-in-with-chatgpt).

### Proposed recipe maintenance

```text
Routine, local:
Feed body -> Public HTML + recipe/parser -> Local Chrome if job requested
                     |                            |
                     +------- Save HTML/text -----+

On a template break:
Grouped failures -> Saved HTML/DOM examples -> Luna/Terra/Sol diagnosis
                                                       |
                                            Versioned recipe proposal
                                                       |
                                      Fixtures + unseen URL validation
                                                       |
                                      Local extraction reuses recipe
```

- Maintain multiple recipes per website when article, opinion, review, live-blog, or video templates differ. Recipes specify matching markers, include/remove selectors, metadata/media selectors, rendering steps, and completeness checks.
- Diagnose failures once per affected template; do not call a model for each failed article. Code preserves the original article body exactly; a model proposes extraction rules, never missing text.
- Validate before activation, retain version/rollback information, and reprocess affected items within queue budgets. Alert on template drift so recipes can be repaired when needed.
- **Status:** Assessment and revised plan saved; no model requests, paid benchmark, or extraction implementation performed.

## 8. Coverage target: evidence still required

- **>90% overall full-text coverage is unproven.** Reader source-code inspection and available rule counts do not establish an achieved success rate.
- Separately measure: (a) full bodies obtained / all collected text-bearing articles; (b) complete extraction / articles whose full body is independently verified accessible through feeds or public pages. Keep restricted and blocked items in (a); display pending counts. Report video-only items and undated archives separately.
- A browser may recover some HTTP-blocked or JavaScript-dependent pages. Recipes cannot recover full text absent from every accessible response; model diagnosis does not change that access ceiling.
- Illustrative ceiling: 85% accessible bodies × 97% successful extraction = 82.45% overall coverage. These are example inputs, not measurements. At 97% extraction success, >90% overall requires >92.8% accessibility.
- Start with 5–10 recent articles per publisher, adding examples for each template. Use a representative validation sample separate from difficult diagnostic cases and recipe-development fixtures. Verify beginning/middle/end retention and clutter, then expand the sample and monitor recent volume-weighted and publisher-level rates before claiming sustained >90% performance.
