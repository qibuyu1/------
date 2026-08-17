# V21 Release Notes

## Search recall repair

- Compact, multi-query planning replaces prompt-like Tavily queries.
- Added data-governance topic-family matching so concrete events survive without the umbrella phrase.
- Added three concrete domestic news lanes for data rights/public data, Data Elements ×/trusted data spaces/assets, and AI data/security/corpora.
- Tavily news uses `topic=news`; paper retrieval uses supported `topic=general`.
- `chunks_per_source` is only sent for advanced/quality search; fast zero-result recovery retries with basic without advanced-only parameters.
- Local date filtering keeps upstream-filtered rows that have no parseable publication date and marks them `dateUnverified`.
- High-confidence indexed results can remain usable when anti-bot blocks origin-page fetching; title mismatch/error/aggregator rejection remains strict.
- Optional Serper web/news fallback activates only when Tavily's post-gate candidate pool is too thin.

## Image sourcing

- Existing Serper/Google Images path remains the only article image provider.
- When cited sources exist, image slots receive a source-title hint; precise mode first searches the original report title + “配图”.
- Authority mode now recognizes additional Chinese news/industry domains and WeChat source pages.
- UI wording changed to “原始报道 / 权威来源优先”.

## Publishing layout

- DOCX/PDF export reworked against the supplied 数治周报 reference: Letter size, brand header/footer, title/deck/meta hierarchy, numbered sections, image/text lead layout, observation callouts and per-section sources.
- Browser preview and copied WeChat rich text now use the same blue/ink/grey palette, section numbering, Songti body and observation box treatment.

## Tests

Regression coverage now includes concrete data-governance headlines, missing-date retention, Tavily topic/depth contract, export media and existing source/image safeguards.
