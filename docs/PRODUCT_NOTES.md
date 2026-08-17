# 数据要素治理 · V9 产品说明

## Deliberate product decisions

- "Latest" is a default ranking behavior, not a narrow date gate. Users can still request explicit 24h / 7d / 30d / custom windows.
- News read counts are never invented. If an upstream source does not provide the metric, the UI says so explicitly.
- Demo/fallback content is visually marked. It is used to keep the product operable when APIs are unavailable, not to impersonate live research.
- Uploaded files are treated as user-selected evidence, not as independently verified authoritative sources.
- Revision is constrained to the original evidence set, which is safer than asking the model to rewrite from memory.
- Image placement is part of article composition. Serper / Google Images is the only online image-search provider; exporters receive the same block sequence used by the web preview.

## Useful next production upgrades

- Persist articles, versions and user files in PostgreSQL / object storage instead of in-memory TTL storage.
- Use a task queue + SSE/WebSocket progress for true server-side progress instead of staged client progress.
- Add OCR for scanned PDFs.
- Add source-level copyright / license metadata for images.
- Add full-text retrieval for paywalled / JS-heavy pages through licensed providers.
- Add organization style packs learned from approved historical articles (prompt/RAG style guide, not hidden model fine-tuning).
- Add WeChat draft API publishing after account authorization.


## V14 优化说明

- 默认首稿使用 DeepSeek V4 Flash 非思考模式；仅在本地质量门禁失败或用户选择深度精修时启用思考模式，减少等待与 Token 成本。
- 写作证据包默认压缩到 6 个来源、单来源约 3200 字符；论文/网页正文抽取默认 1 个 chunk。
- Serper 图片检索默认每个图片槽仅发 1 次搜索，只有找不到可用结果时才执行 1 次备用查询；候选数量与下载探测量同步缩减。
- 来源验证增加 HEAD 快速探测并缩短超时，仍保留严格来源验证。
- 首页推荐由 4 路搜索收敛为 2 路高覆盖搜索，降低首页首屏等待。
- 本地质量门禁只在发现长度、结构、JSON污染或高频模板句时追加修复调用。
