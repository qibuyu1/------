# V31 Release Notes：生成链路解耦与 524 超时修复

V30 的混合视觉方向本身保留，但它把视觉策划字段塞进了首稿 JSON，并且文章生成仍通过一个同步 HTTP 请求等待 DeepSeek 完成。在带 Cloudflare / 反向代理的部署里，写作、证据水合、可能的编辑修复累计超过代理等待上限时，浏览器会直接看到 524；同时视觉后台线程过早启动，在小规格机器上也可能和首个文章响应争抢 CPU。

V31 的目标不是回退图片功能，而是让各部分重新分工：**写作只写作，视觉只在文章完成后工作。**

## 1. /api/generate 改为后台生成任务

- `POST /api/generate` 立即创建任务并返回 `202 + generationJobId`。
- 浏览器轮询 `GET /api/generation/<jobId>` 获取 `pending / running / ready / error / cancelled`。
- 完整 DeepSeek 调用、自动证据补充、来源水合、必要的编辑/长度修复都在后台 worker 中执行。
- 浏览器不再维持几十秒到数分钟的单条 HTTP 长连接，因此代理 524 不会再把仍在执行的生成误判为“整篇失败”。
- “停止生成”会中止前端轮询并把任务标记为 cancelled；已经进入上游 API 的单次调用无法强制杀死，但结果会被丢弃，不会覆盖当前稿件。

## 2. DeepSeek 首稿移除视觉 DSL

V30 要求首稿同时返回 `visualIntent / visualType / visualPlan`，增加了 JSON 复杂度和模型输出负担。V31 移除这些要求：

- 首稿只返回 `understoodBrief / titleCandidates / recommendedTitle / deck / markdown / socialSummary / keyClaims / riskNotes / sourceNotes`。
- 封面、配图位置、真实图或代码图选择全部在首稿完成后本地推断。
- 不再为了画图制造标题、段落或额外模型输出。

## 3. 写作规格与视觉规格隔离

`generationMeta.writingSpec` 只保留文章类型、读者、篇幅、语气、结构、开头、段落、证据、结尾等写作参数。

图片数量、智能混合/真实优先/解释图优先/全部代码图/仅真实图、匹配模式和来源策略进入独立的 `generationMeta.visualSpec`。因此长度修复和编辑修复 Prompt 不再携带图片设置。

## 4. 源新闻图片绑定改为本地恢复

移除模型视觉 DSL 后，源图能力不能下降，所以 V31 增加了更可靠的本地绑定：

1. 如果正文配图锚点出现 `[n]`，优先绑定 source #n；
2. 修复 `。“[2]”` 类句尾引用被切成独立碎片、导致锚点丢失编号的问题；
3. 没有引用编号时，继续用当前段落、来源标题、摘要、`sourceNotes.whyUsed` 做语义匹配；
4. 绑定来源后仍沿用 V29/V30 的来源页原图、二维码/Logo/无关图过滤与 Serper 兜底。

## 5. 视觉任务给正文交付让路

`_start_visual_job()` 改为正文生成完成后延迟约 0.8 秒再进入视觉线程池。这个延迟不是为了“慢”，而是让：

- generation job 先标记 ready；
- article JSON 先被浏览器拿到；
- 小规格 CPU 不会同时做 JSON 序列化和多张 1600×900 PNG 渲染。

之后配图仍然在后台独立更新，不阻塞用户先阅读文章。

## 6. 部署缓存兼容

`compose.html` 给 `styles.css / common.js / compose.js` 增加 `?v=31`，避免部署后浏览器短时间继续使用 V30 缓存脚本，却访问 V31 的异步生成 API。

## 7. 自查

- Python compileall：通过
- 前端 JavaScript `node --check`：通过
- 单元 / 回归 / 服务 smoke：127 passed
- `scripts/quality_benchmark.py`：passed
- 查询主链预算保持 Tavily 5、Serper 搜索兜底最多 3
- 图片无来源首轮 Serper Images 仍最多 1；已有来源页图片仍为 0
- 智能结构图与全部代码绘图仍可 0 次 Serper Images

最终 ZIP 会再次解压到干净目录复测后再交付。
