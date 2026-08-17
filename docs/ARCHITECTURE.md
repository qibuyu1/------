# 数据要素治理 · V29 架构说明

## 1. 交互式检索：短 Query、多路召回、国内优先

`browser -> /api/search -> pipeline.research`

V24 延续 V23 的生成质量约束，并继续优化检索：不再把“主题 + 年份 + 媒体要求 + 政策/实践/案例”等条件堆成一个长句。检索意图先被拆成 3—5 个词的短 Query；对于“数据要素治理”这类上位主题，还会展开为数据产权登记、公共数据、数据流通、数据资产、可信数据空间、数据要素×、AI 数据、语料、数据安全等主题家族。

国内优先的多路查询包括：

- 国内新闻：权威媒体域名与开放中文新闻两路；
- 国内政策：政府域名定向检索；
- 中文论文；
- 国际新闻、政策与论文补充；
- 当 Tavily 候选不足时，若已配置 `SERPER_API_KEY`，条件式补充 Serper / Google News 与 Web Search 结果。

Tavily 交互检索保持轻量：fast 深度、不生成 answer、不拉取图片；0 条时使用 basic 做一次恢复。论文仍走 Tavily，但使用 Search 支持的通用 topic，而不是自造 `paper` topic。

## 2. 本地相关性门禁：主题家族而不是完整短语

`provider rows -> query/topic-family scoring -> time filter -> source verification`

“数据要素治理”是站点的总主题，而真实新闻标题通常只出现具体事件。因此当前相关性判断以主题家族为主：例如“全国统一数据产权登记”“数据要素×大赛”“算力券/模型券/语料券”“AI 数据与安全”都可以在不逐字出现“数据要素治理”的情况下通过门禁。

单独出现泛化词“数据”不会被判定为高相关，避免把数据中心、存储硬件等无关报道混入结果。

## 3. 时间过滤：避免把上游已过滤结果二次清空

搜索提供商已经收到近 7 天 / 近 30 天等时间条件。如果返回项缺少标准化发布日期，当前版本不再直接删除，而是保留并标记 `dateUnverified=True`。有明确日期且超出范围的结果仍会剔除。

这个策略专门解决国内媒体、政府站点、微信公众号等经常“有标题和原文 URL、但搜索结果没有规范日期字段”的情况。

## 4. 来源核验：verified / indexed 两级状态

`search row -> canonical/original URL probe -> verified | indexed | rejected`

- `verified`：原文可以读取，标题/页面与搜索结果一致；
- `indexed`：搜索引擎已明确定位原始 URL，标题和主题高度一致，但网站反爬或拒绝自动抓取；
- `rejected`：404/410、聚合页、搜索导航页、标题错配、明显错误页或无关内容。

当前版本允许高置信 `indexed` 来源作为可用证据候选，并在生成阶段再次尝试 Extract；这避免把国内媒体反爬误判成“新闻不存在”。

## 5. 条件补充检索：Tavily 主检索 + Serper 兜底

`Tavily candidates -> local gate -> verification -> usable-count check -> optional Serper supplement`

Serper 不是无条件混用，而是两处按需补充：

1. Tavily 本地门禁后候选太少；
2. Tavily 候选虽然不少，但经过原文核验后可用条目又降到阈值以下。

补充结果仍要经过相同的语义门禁、来源核验、去重和排序，不会直接绕过质量控制。

## 6. 选中来源后的正文抽取

`selected search rows -> Tavily Extract (batch) -> DeepSeek evidence pack`

完整网页抽取延迟到用户真正生成文章时再做。检索页只展示轻量结果；被选中的新闻/政策/网页若正文较薄，再批量执行 Tavily Extract。上传文件和论文摘要保留各自已有文本。

这样既降低交互检索延迟，也避免为用户不会采用的结果支付抽取成本。

## 7. 写作与修订

`evidence -> optional hydration -> DeepSeek draft -> editorial gate -> visual planning -> ArticleStore`

写作规格会把文章类型、目标读者、篇幅、语气、标题偏好、结构、开头、段落节奏、证据表达和结尾方式转换成明确规则。深度模式可执行二次主编精修；长度不达标时再做有限次数的长度修复。

修改支持句子、段落、章节和整篇范围，并保留撤回与恢复初稿历史。

## 8. 图片：来源页元图 + 小节语义路线 + 条件补搜

`article/sourceList -> visual slots -> source images / source-page metadata -> compact semantic query -> Serper / Google Images -> semantic + download filter -> embed`

当章节引用了具体新闻、政策或案例时，V27 会并行使用两类零搜索成本的来源图：Tavily Extract 已返回的页面图片，以及直接从来源页 OpenGraph / Twitter / JSON-LD / 正文大图元数据发现的候选；两者都要继续经过尺寸、可下载性、重复度和语义检查。

没有可靠源图时，图片 Query 不再统一追加“科技研发”。系统根据当前小节含义选择视觉路线，例如“数据治理失败”映射到数据孤岛/数据质量/数据标准，“影响普通人”映射到个人信息保护/隐私/数字政务，研发、业务、安全、公共数据、资产化、可信数据空间等分别有独立路线。自然语言小标题与长段落只用于理解，不整句发送给图片搜索。

正文图片保持 V26 的快速路径：无源图先发 1 条高信息 Query；只有该位置没有可用图片时才补第 2 条不同 Query。所有缺图位的补搜与下载探测并行执行，因此提升召回不依赖串行等待。图片仍过滤 logo、头像、模板图、低尺寸、重复、失效以及笔记本/显卡/CPU 等与数据主题无关的泛科技干扰图。

是否具备转载权仍需发布者依据来源授权和平台规则判断；系统只负责检索、来源标注和技术嵌入。

## 9. 普通文章渲染与多格式导出

网页预览、公众号富文本、HTML、DOCX 和 PDF 都以项目最终 block stream 为内容来源。V23 不复刻周报模板，而只保留成熟中文文章的基础排版：

- 自然标题与导语；
- 黑体类标题、宋体类正文；
- 自然二级标题，不强制 `01 / 02 / 03`；
- 图片按正文位置嵌入并保留图片来源；
- 默认不显示参考来源；只有用户显式开启正文来源编号时才附来源清单。

远程图片在导出前会防御式下载、归一化和缓存，然后嵌入 DOCX/PDF。导出文件继续执行结构检查。

## 10. 首页与缓存

`browser local cache -> /api/home-feed -> server cache -> research`

浏览器优先绘制上次成功的真实 feed；首次没有 feed 时展示真实专题入口而不是演示新闻。服务端研究缓存按查询计划版本化，避免继续命中旧版错误 Query 的缓存快照。

## 11. 真实性原则

项目不注入演示新闻、演示论文、虚构来源或伪造外链。没有真实结果时保持为空，并向用户区分：API 未配置、上游无结果、网络/配额错误、或来源核验被拒绝。
## 12. V23 生成链路补充

### 自动证据

`compose topic + writing brief -> frontend research -> server fallback research -> balanced evidence -> sourceList -> UI evidence sync`

前端自动研究不是唯一入口。生成端看到 `autoEvidence=true` 且证据仍为空时，会再次使用主题与写作 brief 做相关性优先检索，并只在候选不足时放宽一次。最终采用来源反向同步到生成页，保证“自动补充”对用户可见。

### 默认结构质量门禁

默认结构没有固定章数。Prompt 优先 0—3 个自然小标题，本地门禁会识别“问题/做法/机制/影响/条件/判断”等模板化标题；默认结构出现四个以上整齐章节时，也会进入一次编辑修复。显式选择某个结构模板时，才按用户选项执行。

### 图片

`final title/section/adjacent paragraph -> strict semantic image query -> Serper -> metadata relevance gate -> download probe -> embed`

封面不进入 Serper 链路，而由最终标题与 cover brief 生成固定编辑设计图。正文图片必须同时满足章节语义和图片元数据语义；数据类文章额外要求候选自身带有数据/模型/算法/AI 等上下文。语义不足时槽位留空。

### 导出

DOCX、ReportLab PDF 和 Pillow PDF fallback 使用同一文章 block stream，并尽量保持一致的标题层级、正文 11pt 左右的阅读尺度、中文行距、图片说明和页边距。fallback 只在缺少 ReportLab 时启用，但仍保证成熟中文长文的基本排版。


## 13. V24 检索与源图链路补充

### 长自然语言 Query

`user query -> concept extraction -> short semantic variants -> parallel news/general discovery -> local semantic gate -> source verification`

V24 对“数据在技术突破上的作用”这类关系型自然语言问题，不再把整句同时当成搜索词和本地硬匹配条件。`query_intent.py` 会抽取可检索概念，并生成少量不同意图的短 Query。概念型请求主要并行走开放新闻与国内普通网页发现，避免重复请求同一类新闻索引。

普通网页发现结果进入统一分类器后，再按政府域名、学术特征、媒体特征归入政策、论文或新闻；因此搜索页仍使用同一套结果模型、相关性门禁、核验和去重逻辑。

当 Tavily 召回仍不足时，Serper Web 与 News 以最多三条短 Query 并发补充。兜底结果不会绕过本地质量门禁。

### 来源页图片

`adopted sources -> batch Tavily Extract(include_images) -> sourceImages -> section/source matching -> visual candidate ranking -> final visual`

最终被文章采用的网络来源在正文 hydration 阶段一次性批量提取正文和来源页图片。来源图片随 `sourceList` 保存，并根据章节标题、相邻正文与来源标题/摘要的重合度绑定到具体图片槽位。

`visuals.py` 会优先检查绑定来源页中的真实图片；只有没有合格原图时才进行 Serper / Google Images 搜索。来源明确时首条图片 Query 使用原文标题与来源名称，而不是把整段正文或“原文 配图”等弱索引词塞进搜索。

所有最终视觉结果（系统封面与正文图片）统一进入 `visualPlan / visualReport`，前端“配图与来源”面板据此显示缩略图、类型、来源、尺寸、匹配分和来源链接。正文图片数量仍只统计正文槽位，封面不占用户设置的正文图片数量。

### 延迟控制

V24 的速度优化不是减少质量门禁，而是减少重复检索：

- 概念型 Query 不再重复跑价值接近的权威新闻专线；
- 新闻与普通网页并行；
- Serper Web / News 并行；
- 来源正文与来源页图片在同一次批量 Extract 中获取；
- 有来源页图片时只保留一条 Serper 图片保险查询，无来源图时才并行两条不同语义的图片查询。


## 14. V25 预览等高与来源绑定配图

桌面端 `compose.js` 使用 `ResizeObserver` 读取 `.compose-settings` 的真实高度，并通过 CSS 变量 `--compose-column-height` 让 `.article-side` 与左栏等高。右侧工具栏、导出区和进度条固定占据自己的高度，活动 `.tab-panel` 使用剩余空间并独立滚动。900px 以下关闭这一同步逻辑。

图片槽新增可选 `sourceId`。`content_blocks.plan_visual_slots()` 保留该字段，`pipeline._apply_visual_layout()` 优先按编号绑定 `sourceList` 中的确切来源，并把来源标题、来源域名、来源页图片池传给 `visuals.resolve_visuals()`。没有显式编号时，仍会结合当前小标题、段落、来源摘要和 `sourceNotes.whyUsed` 做语义匹配。

`visuals._visual_query_variants()` 不再拼接长自然语言，而是把主题、图片槽查询和小标题压缩成短核心短语。存在来源绑定时，先生成“原文标题 + 来源名”和“原文标题 + site:域名”两条定向路线，再准备数据科技场景回退路线。

未命中的正文图片槽仍保留在 `visuals` 列表中，但 `image=None`、`matchedBy=unresolved`。因此正文不会插入无关图，而“配图与来源”面板仍能完整显示计划与失败位置。


## 15. V26 质量守恒层

### 关系型语义门禁

`natural-language query -> deterministic concept groups -> short diverse provider queries -> concept-side coverage gate`

关系型主题不再依赖整句包含或宽松的中文二元词重合。每个重要概念拥有少量高频别名，候选结果至少覆盖两个语义侧才通过。该层完全本地运行，不增加 API 调用。

### 搜索预算控制

`Tavily primary -> conditional Serper (2 web + 1 primary news) -> verify -> at most 1 unused web retry`

重复 Query 被记录并禁止在来源核验后再次付费搜索。普通网页通道保留，因此降低调用数量不以牺牲长尾网页召回为代价。

### 写作质量门禁

`draft -> local quality gate -> optional targeted editorial pass -> one length repair if needed`

本地门禁检测结构模板化、泛化标题公式、连接词重复、碎片段落和内部字段污染。只有命中明显问题时才追加编辑调用。证据摘要在进入模型前做本地高信号句抽取，降低 Prompt 体积。

### 图片第一拒绝权

`sourceId/source page images -> local semantic/download gate -> optional one Serper query -> one distinct fallback`

来源页原图通过时图片搜索调用为零。去重阶段保留来源页图片 `position`，第 7 张以后不再仅凭同页来源通过，降低相关卡片与装饰资源误配。

### 统一导出版式

`article block stream -> DOCX canonical layout -> LibreOffice PDF when available -> ReportLab fallback`

PDF 主路径不再维护一套与 Word 分离的视觉参数；DOCX 和 PDF 共享页面、字体、段落与图片布局逻辑。

### 离线质量基准

`scripts/quality_benchmark.py` 固化搜索分离度、Serper 调用数、图片调用数和写作 Prompt 体积代理，作为后续迭代的非回退门禁。


## V28 自查补充

视觉层新增“来源页无 Serper 仍可回源”“多图片位公平探测”“现代 `srcset` 解析”和更严格的来源页身份判定；检索意图层把单字语法词与概念拆分彻底分离，避免数据中心/数据中台等术语被误判。结果 ID 使用稳定摘要，中文 Serper 日期先本地标准化再进入新鲜度计算。以上改动均不增加正常路径的 provider 调用预算。

## V29 图片精准度补充

正文配图链路新增两道本地门禁，不增加 LLM 或搜索 API 预算：

`candidate URL/meta -> source-page hygiene -> download/profile -> QR artifact gate -> paragraph-scene semantic gate -> ranking`

- `source_page_images.py` 在解析页面时利用 URL、alt、class/id 过滤二维码/公众号/扫码资源；
- `image_fetch.py` 对已下载候选执行轻量 QR finder-pattern 检测，解决随机 CDN 文件名二维码；
- `visuals.py` 不再把站点根首页视为具体证据页，首页图片不能获得 exact-source 旁路；
- 图片语义从“文章总主题”继续下沉到“当前段落业务场景”，仓储、供应链、制造、销售、财务、医疗、交通等使用独立短 Query 和局部锚点；
- 对 Serper 泛图，若段落存在明确场景，则必须命中该局部语义。源新闻真实照片仍允许不在 caption 中逐字写“数据”，但仍必须先通过视觉卫生检查。

因此 V29 主要减少错误候选，而不是靠增加搜索次数补救；现有 provider 调用上限保持不变。

