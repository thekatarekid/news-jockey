# News Jockey — Project Brief

Read this file before planning work in a new Codex chat. A new chat does not automatically inherit the full prior conversation. Use English for all written communication.

## Confirmed context and authorization

- Project name: **News Jockey**. Solo founder: **Aditya Katare**.
- The immediate requested focus is a **configurable news collector**, before bulletin creation.
- The user wants to add news sources and understand the freshest practical collection approach.
- This folder is a local Git repository with no remote repository. On 2 October 2026, the user explicitly authorized building a local Mac web portal and collector with 50 initial sources (ten per category), article tables, filters/sorts, searchable source suggestions and bulk URL addition. The implementation now exists; see README.md and SOURCES.md.
- Codex UI opening was blocked by tool access. The user said they would open the folder themselves; completion of that step is unverified.
- The user's later instruction authorized local implementation and running the app. No publishing, Git commits, remote setup or Codex settings change was requested. This brief alone is not authorization for additional work.

## Proposed product direction — not finalized

A US-focused AI news publication that curates news across publishers and presents facts, different perspectives, evidence, and unknowns with source attribution. AI does not guarantee unbiased news.

Text, audio, and video were explored. The latest interest is Pocket FM-style daily audio news. Possible features include an 8–10 minute bulletin, chapters, background listening, playback resume, linked sources, and social video clips for discovery. These formats and features have not been validated or finalized.

The proposed $10/month subscription and target of 2,000 paying users are hypothetical goals, not traction or established economics.

## Current collector scope

- Existing local Flask/SQLite portal, RSS/Atom, broad section feeds, and bounded sitemap discovery. Source category never restricts article content.
- Routine extraction must use free local code: no official news APIs/licenses, paid search/scraping/proxies/hosted browsers, or per-article models.
- Original feed HTML, sanitized article HTML/plain text, attributed images, content types, video/audio metadata, public captions, and article versions are retained.
- Automatic ordinary extraction only covers publication dates today + yesterday in the portal timezone (default Asia/Calcutta). Old/undated items wait for **Extract old articles**. Two separate Chrome buttons use the same recent/older date groups.
- Installed Chrome runs as a separate temporary headless process only for explicit jobs. Personal Chrome tabs/profile/cookies are never attached.
- Recipe diagnostics, declarative editing, held-out validation, activation/rollback support occasional optional model-assisted template repair. Extraction itself makes no model calls.
- >90% overall full-body coverage is a target, not verified performance. Structural full labels do not prove publisher completeness; access restrictions remain visible.
- On 7 October, the user authorized Phases 3–5: classification, multi-label news categories, story/topic grouping, and separate phase tabs. Implemented with local EmbeddingGemma 2 plus explicitly requested subscription Luna/Terra batches. Sol/Astra, Jev, paid LLM/API services and automatic escalation are excluded. Phase 6 outputs and Phase 7 publishing review remain planned. See progress.md for live verification and quality limits.

## Open product and engineering decisions

- Expand publisher inventory and validate current templates; API integrations are excluded.
- Desired freshness and acceptable operational costs within provider limits.
- Collector interface/storage/runtime are now a local Flask browser portal, SQLite and Python, served at http://127.0.0.1:8765. No online hosting is requested.
- App versus website remains open.
- Phone-number login was suggested, not finalized.
- Website subscription followed by app sign-in is being considered. Platform, payment, and legal compliance must be checked before implementation.
- Final audience, format, bulletin duration, and publication cadence remain unvalidated.

## Distribution and economics explored — assumptions only

Organic discovery from zero through Instagram Reels, YouTube Shorts, and TikTok where legitimately available was explored. Several category or regional accounts were proposed. Identical duplicate clips do not guarantee incremental reach and can trigger platform rules. A scenario using four original clips daily is an assumption, not a chosen publishing schedule.

Organic forecasts are conditional models, not measured performance. Paid-ad CPI scenarios of $2/$5/$10 and install-to-paid rates of 1%/3%/5% are sensitivity assumptions, not verified platform benchmarks. No accounts, posting, ad purchases, or campaigns were authorized or executed.

## Tax question — unresolved

An Indian tax investigation considered a potentially eligible publishing-business classification under presumptive provisions. Exact eligibility, including whether declaring 6% despite high actual margins is appropriate, remains unresolved. The conditional low-tax illustration must not be treated as guaranteed business economics. No tax filing, professional engagement, or incorporation was authorized.

## Next step for a new Codex chat

Read this brief, README.md and SOURCES.md, then inspect the implementation and follow the user's current request. Read `news_processing.md` and `progress.md` as well. On 6 October 2026, the user authorized executing Phases 1 and 2 and maintaining `progress.md`, including past work. Publisher/channel discovery, content/media labels, public-page extraction, image attribution, video metadata, article versions, and historical backfill are now implemented. Coverage scans and recent extraction run while the local server/Mac are awake; archives require manual jobs. publisher access and inventory gaps remain visible. Website/feed URLs can still be added in batches of up to 50. Phases 3–5 are implemented with separate review screens and the free/subscription constraints above. Phases 6–7 and bulletin production remain later implementation.


## Current implementation update — 7 October 2026

Calendar-month extraction replaces today/yesterday windows. Current-month extraction is automatic; selected/previous months and optional Chrome have explicit controls, with unknown dates separate. Derived experimental processing was backed up and reset. Whole-month local GLiNER2 metadata and EmbeddingGemma 2 event grouping are rebuilding October. Topics and generated outputs are deferred pending story-quality review. See `news_processing.md` and `progress.md` for the active specification and evidence.
