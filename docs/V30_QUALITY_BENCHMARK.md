# V30 质量 / 成本守恒基准

运行：

```bash
python scripts/quality_benchmark.py
```

该脚本不调用真实网络或付费 API，通过 mock 固定比较检索精度、provider 调用预算、图片路由和写作 Prompt 体积。

当前基准：

- 关系型查询正负样本最小分离 margin：86
- Tavily 主链：5 次
- Serper Web/News 兜底：3 次
- 无来源正文图初始 Serper Images：1 次
- 来源页已有可用原图：0 次
- 智能模式结构图：0 次 Serper Images，直接 `generated-diagram`
- 全部代码绘图：0 次 Serper Images
- 真实优先失败：2 次以内不同图片 Query，随后本地代码图兜底
- 首稿 Prompt 字符代理：13,582

这些值作为后续迭代的非退化门禁：提升视觉质量不应靠无边界增加搜索次数或写作 Token。
