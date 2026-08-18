# V31 质量 / 协调守恒基准

V31 不是削弱 V30 的视觉能力来换稳定，而是增加两条新的硬约束：

1. **文章生成 HTTP 请求必须快速返回任务 ID**，慢模型不能直接暴露为代理 524；
2. **视觉规划不能进入首稿 LLM 输出合同**，写作和视觉各自独立。

离线质量基准继续检查：

- “数据在技术突破上的作用”相关 / 无关结果区分 margin >= 86；
- Tavily 主检索 5 次；
- Serper Web/News 兜底最多 3 次；
- 无来源正文图初始 Serper Images <= 1；
- 有来源页图片初始 Serper Images = 0；
- `smart` 明确结构段落可 0 次 Serper 直接生成代码图；
- `all_diagram` = 0 次 Serper；
- `real_first` 找不到可靠真实图后仍能代码图兜底；
- 文章 Prompt 不包含 `visualPlan / visualType`；
- generation job 启动函数在慢 worker 场景下立即返回，随后轮询拿到结果。

运行：

```bash
python scripts/quality_benchmark.py
python -m pytest -q
```
