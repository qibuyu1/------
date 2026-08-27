# 标题去 AI + 小标题可靠加粗 + 页脚品牌（t18）

> 目标：①不同 titleMode 真正产生“不同、去 AI、有阅读动力”的标题；②PDF 主小标题（H2，带 01/02）跨环境**一定加粗**；③页脚品牌统一为“数据要素治理”。
> 实现于 `C:\Users\a1370\Desktop\数据要素治理`（只改本项目，未调外部 API）。与 `scripts\verify_layout.py`、`data\exports\sample_layout.docx/.pdf` 一并产出。

## 1. 改动文件 / 函数清单

| 文件 | 改动 |
|------|------|
| `app\pipeline.py` | `_control_instruction` 的 `title_rules`：7 种 titleMode 各自给出**明确、具体、差异大**的标题约束；`_rank_wechat_titles`：新增 `_AI_TITLE_WORDS` + `_is_ai_title()` 本地标题门禁，强化“具体对象+数字+变化/冲突/疑问”打分、弱化“大词+口号”，并把 AI 候选从排序池中剔除（有具体候选时）。 |
| `app\exporter.py` | `_build_pdf_reportlab`：新增 `_pdf_heading_image()`（PIL 描边加粗，找不到粗体 TTF 时用）与 `_pdf_heading_flowable()`（有粗体字体用 Paragraph 真粗体，否则用描边加粗图），保证 H2/H3 跨环境加粗；新增 `use_bold_font` 分支。`build_docx` 的 Heading 样式已 `bold=True`。 |
| `app\exporter.py` /`app\cover_art.py` | 全部“数治攻关”→“数据要素治理”（PDF 作者元数据、reportlab 页脚、回退渲染页脚、封面页脚）。 |

## 2. 标题去 AI / 尊重标题方式

**a) 每个 titleMode 明确且差异大**（`title_rules`，写进写作 prompt 的“标题执行”行）：
- 默认·自然起题：具体对象+正在发生的事/具体反差/疑问；禁止“数据要素治理：…”前缀与空词。
- 公众号吸引力型：具体对象开路，紧跟变化/反差/疑问钩子（只用一次）；禁止空壳词。
- 信息密度型：30 字内给出“对象+具体数字/规模/时间+方向”；禁止只写大词。
- 问题型：可被回答的真问题（含对象+矛盾）；禁止“如何看待/何去何从”；至少两个标题候选使用真实问题句。
- 趋势判断型：有边界的趋势判断（谁在变/方向/约何时/多大规模）；用“正在/开始/不再”，不用绝对词。
- 政策解读型：点名“哪份政策/文件/机构 + 对谁 + 改变什么”；禁止“重磅/重大利好/一图读懂”。
- 克制的吸引力：一个对象+一个具体变化/疑问；无感叹号、无第二人称。

**b) 本地标题门禁**（`_is_ai_title`）：命中 `一文读懂/深度解析/全面解读/全景观察/正在重塑/新范式/新逻辑/数字化转型/数字化浪潮/赋能/引领/新趋势/未来已来/行业级/一站式/全链路/智慧化/转型升级/高质量发展/洞察` → 判为 AI 标题。
- `_rank_wechat_titles`：对 AI 标题 -9 分；只要存在非 AI 候选，就将 AI 候选整体剔除排序池（`concrete if concrete else cleaned`），确保 `recommendedTitle` 是具体、有对象、有变化/冲突/疑问的。
- 打分强化：数字/百分比 +2.2/+1.0，疑问/冲突/变化标记 +1.8/+1.6，具体对象 +1.1，15–30 字 +2.0；弱化（扣分）：大词口号（赋能/引领/加速/浪潮/大势/蓝海/风口）+ 重磅/颠覆/惊天点击党、`“A：B”` 排比模板。

**c) 离线短测**（无 API）：`_is_ai_title` 正确判 True：<br>“一文读懂数据要素治理 / 数据要素治理的深度解析 / 数据要素数字化转型浪潮”；<br>判 False：<br>“企业数据安全升格为一级部门，为什么值得警惕 / 上海推算力券，把数据成本打下来”。<br>`_rank_wechat_titles` 去除 AI 后排出：<br>①为什么大厂把数据安全升格成一级部门 ②央企财务数据治理，为何是块硬骨头 ③全国统一数据产权登记：从政策到落地 ④上海发算力券：企业的数据成本降了多少。每个 titleMode 的“标题执行”指令各不相同。

## 3. 小标题可靠加粗（PDF H2 一定加粗）

- **优先真正的粗体字体**：`_find_cjk_bold_font()` 搜 `msyhbd.ttc / NotoSansCJK-Bold.ttc/.ttf / simhei.ttf / simhei.ttc / msyh.ttc / PingFang.ttc`，`_register_pdf_heading_font()` 用 reportlab `TTFont` 注册为 `CJKHeading`（TTC 按 subfontIndex）→ 标题用 Paragraph 真粗体（可选中文本）。本机命中 `C:\Windows\Fonts\msyhbd.ttc`。
- **跨环境兜底（关键）**：`_pdf_heading_flowable()` 在 `heading_font != STSong-Light`（即未注册到粗体）时，改用 `_pdf_heading_image()` 把标题渲染为 PIL 位图——含粗体 TTF 就用它，否则用 `ImageDraw.text(..., stroke_width, stroke_fill)` **描边加粗**，保证无粗体字体环境标题仍**一定加粗**。
- **DOCX**：Heading 样式 `bold=True` + 标题 run `bold=True`（H2 带 01/02 序号，H3 加粗不加号）。
- 标题 keep_with_next；字号/间距保持现状；正文与配图未改动。

## 4. 页脚品牌 → “数据要素治理”

- `exporter.py`：`author="数治攻关"→"数据要素治理"`（PDF 元数据）、reportlab 页脚 `canvas.drawString(..., "数据要素治理")`、回退渲染页脚 `draw.text(..., "数据要素治理")`。
- `cover_art.py`：封面页脚 `"数治攻关 · 编辑封面"→"数据要素治理 · 编辑封面"`。
- 已 `grep` 确认 `app/` 下无残留“数治攻关”（仅 docs 历史审计文档里仍保留该词作“改前”说明，非代码输出）。

## 5. 测试结果

- `python -m unittest tests.test_core tests.test_v33`（原 venv，先临时移走 `.env`，跑完恢复）→ **Ran 45 tests，44 通过，1 个既有失败** `test_home_feed_never_injects_demo`（读 `data\home_feed_cache.json` 磁盘快照，与本任务无关、与基线一致）。通过率未降低。
- 标题相关回归：`test_generic_site_topic_is_not_forced_into_title_prefix`、`test_quality_gate_repairs_generic_ai_title_and_repetitive_connectors`、`test_writing_controls_enter_prompt`（问题型规则仍含“至少两个标题候选使用真实问题句”）→ 全部通过。
- `scripts\verify_layout.py`（离线，强制 reportlab）**全部 True**，含：H2 带“01 机制如何运转”+加粗(`<w:b/>`)、H3 加粗不加号、`PDF embeds bold TrueType font`、`footer brand (neutral 数据要素治理)`、11.5pt/行距1.5/首行460/两端对齐/#172033/图片来源/资料来源/回退比例。
- 三个改动文件 `py_compile` 通过；样例已重新生成 `data\exports\sample_layout.docx/.pdf`。

## 6. 目检建议
- Word 打开 `sample_layout.docx`：主小标题“01 …”加粗；三级标题加粗无编号；页脚/眉行品牌为“数据要素治理”。
- PDF 阅读器打开 `sample_layout.pdf`：标题段加粗（有粗体字体环境为真粗体，无则为描边加粗位图）并带 01/02；正文仍为常规字重；页面底部品牌为“数据要素治理”。
