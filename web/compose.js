(() => {
  const $ = GovernanceApp.$;
  const $$ = GovernanceApp.$$;
  const state = {
    sources: [], article: null, topic: "数据要素", controller: null, generating: false, health: null,
    selectedBlock: null, selectedText: "", selectedHeading: "",
  };
  const progress = GovernanceApp.sailingProgress("compose");

  function inlineMarkdown(text = "") {
    return GovernanceApp.escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([0-9]+)\]/g, '<span class="citation">[$1]</span>');
  }

  function renderSources() {
    $("#sourceCount").textContent = `${state.sources.length} 条`;
    if (!state.sources.length) {
      $("#sourceList").innerHTML = `<div class="source-empty"><span>尚未固定证据</span><p>可以从查询页带资料过来、上传文件，或直接点“自动补全”。</p></div>`;
      return;
    }
    $("#sourceList").innerHTML = state.sources.map((src, i) => {
      const uploaded = src.type === "upload" || src.origin === "upload";
      return `<div class="compose-source-row">
        <span class="source-order">${String(i + 1).padStart(2, "0")}</span>
        <div><p><span class="source-badge type-${GovernanceApp.escapeHtml(src.type)}">${GovernanceApp.typeLabel(src.type)}</span><span class="cited-badge">已引用</span>${GovernanceApp.escapeHtml(src.title)}</p><small>${uploaded ? "上传文件" : `${GovernanceApp.escapeHtml(src.source || "来源")} · ${src.sourceVerified ? "已核验原文" : "已定位原文"}`}${src.publishedAt ? ` · ${GovernanceApp.formatDate(src.publishedAt)}` : ""}</small></div>
        <button data-remove-source="${GovernanceApp.escapeHtml(src.id)}" aria-label="移除资料">×</button>
      </div>`;
    }).join("");
  }

  function mergeSources(...groups) {
    const seen = new Set(); const out = [];
    for (const group of groups) for (const src of group || []) {
      const key = src.id || src.url || src.title; if (!key || seen.has(key)) continue;
      seen.add(key); out.push(src); if (out.length >= 16) return out;
    }
    return out;
  }

  function chooseSources(rows) {
    const ordered = [...rows].sort((a, b) => (Number(b.queryMatchScore || 0) - Number(a.queryMatchScore || 0)) || (Number(b.score || 0) - Number(a.score || 0)));
    if (!ordered.length) return [];
    const top = Number(ordered[0].score || 0); const threshold = Math.max(55, top - 14);
    const picked = []; const seen = new Set();
    for (const type of ["news", "policy", "paper"]) {
      const hit = ordered.find((r) => r.type === type && Number(r.score || 0) >= threshold);
      if (hit) { const key = hit.id || hit.url || hit.title; if (key && !seen.has(key)) { picked.push({ ...hit, origin: "auto" }); seen.add(key); } }
    }
    for (const row of ordered) {
      if (picked.length >= 8) break;
      const key = row.id || row.url || row.title;
      if (!key || seen.has(key)) continue;
      picked.push({ ...row, origin: "auto" }); seen.add(key);
    }
    return picked;
  }


  function getBodyImageCount() {
    const select = $("#imageCount");
    if (!select) return 3;
    if (select.value === "custom") {
      const raw = Number($("#customImageCount")?.value || 0);
      return Math.max(0, Math.min(8, Number.isFinite(raw) ? Math.floor(raw) : 0));
    }
    const raw = Number(select.value);
    return Math.max(0, Math.min(8, Number.isFinite(raw) ? Math.floor(raw) : 3));
  }

  function syncImageStrategyControls() {
    const strategy = $("#imageStrategy")?.value || "smart";
    const help = $("#imageStrategyHelp");
    const helpMap = {
      smart: "智能混合：案例、新闻先找原图；流程、机制、数理分析优先绘制；真实图不可靠时自动用代码解释图兜底。",
      real_first: "真实图片优先：先查源新闻和网络图；该位置始终没有可靠图片时，再用代码解释图补足。",
      diagram_first: "解释图优先：流程、因果、对比、分层、时间线、指标分析更积极地直接绘制；具体新闻案例仍尽量保留真实图。",
      all_diagram: "全部代码绘图：正文不调用 Serper 图片搜索，所有图片由本地受控视觉引擎绘制；新闻事实仍来自证据，不伪造现场照片。",
      real_only: "仅真实图片：只使用源新闻 / 源材料原图和 Serper 网络图片，找不到可靠图片就留空，不用代码图兜底。",
    };
    if (help) help.textContent = helpMap[strategy] || helpMap.smart;
    const codeOnly = strategy === "all_diagram";
    ["#imagePreference", "#imageMatchMode", "#imageSourcePolicy"].forEach((id) => { const el = $(id); if (el) el.disabled = codeOnly; });
  }

  let composeHeightObserver = null;
  function syncComposeColumnHeight() {
    const workspace = $(".compose-workspace");
    const settings = $(".compose-settings");
    if (!workspace || !settings) return;
    if (window.matchMedia("(max-width: 900px)").matches) {
      workspace.style.removeProperty("--compose-column-height");
      return;
    }
    const height = Math.ceil(settings.getBoundingClientRect().height);
    if (height > 0) workspace.style.setProperty("--compose-column-height", `${Math.max(640, height)}px`);
  }
  function initComposeColumnSync() {
    const settings = $(".compose-settings");
    if (!settings) return;
    const schedule = () => requestAnimationFrame(syncComposeColumnHeight);
    composeHeightObserver?.disconnect?.();
    if (window.ResizeObserver) {
      composeHeightObserver = new ResizeObserver(schedule);
      composeHeightObserver.observe(settings);
    }
    window.addEventListener("resize", schedule, { passive: true });
    schedule();
  }

  function setComposeProgress(percent, text, title = "正在生成") { progress.set(percent, text, title); }
  function scheduleComposeStages(stages) { progress.schedule(stages, () => state.generating); }
  function finishComposeProgress(text = "图文稿已经整理完成。") { progress.finish(text, "完成", 950); }
  function hideComposeProgress() { progress.hide(); }
  function setGenerating(on) {
    state.generating = on;
    $("#generateButton").disabled = on; $("#autoSourceButton").disabled = on; $("#applyRevision").disabled = on;
    $("#cancelGenerateButton").hidden = !on;
  }

  async function uploadFiles(files) {
    const list = [...(files || [])]; if (!list.length) return;
    if (state.generating) return GovernanceApp.toast("当前任务完成后再上传文件");
    for (const file of list.slice(0, 8)) {
      if (file.size > 12 * 1024 * 1024) { GovernanceApp.toast(`${file.name} 超过 12MB，已跳过`); continue; }
      const form = new FormData(); form.append("file", file);
      GovernanceApp.toast(`正在读取：${file.name}`);
      try {
        const response = await fetch("/api/upload", { method: "POST", body: form });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || data.detail || "文件读取失败");
        const source = data.source; if (!source) throw new Error("文件没有返回可用内容");
        state.sources = mergeSources(state.sources, [{ ...source, origin: "upload", selectedByUser: true }]);
        GovernanceApp.saveEvidence(state.sources); renderSources(); GovernanceApp.toast(`已引用文件：${file.name}`);
      } catch (e) { GovernanceApp.toast(`${file.name}：${e.message || "上传失败"}`); }
    }
    $("#fileInput").value = "";
  }

  function evidenceTimeRange(text = "") {
    return /最新|近期|最近|本周|本月|今年|近\s*\d+/.test(String(text || "")) ? "latest" : "all";
  }

  async function autoCollect({ silent = false, signal = null } = {}) {
    const q = $("#topicInput").value.trim() || "数据要素";
    const description = $("#angleInput")?.value.trim() || "";
    let ownController = null;
    if (!silent) {
      if (state.generating) return [];
      ownController = new AbortController(); state.controller = ownController; signal = ownController.signal; setGenerating(true);
      setComposeProgress(6, "正在解析主题并准备证据检索…", "正在补全资料");
      scheduleComposeStages([[700, 24, "优先检索国内权威新闻与产业实践…", "正在补全资料"], [1800, 46, "正在核对政策文件与原文地址…", "正在补全资料"], [3200, 70, "正在检索中文论文与相关国际研究…", "正在补全资料"], [4800, 88, "正在选择最能支撑文章观点的证据…", "正在补全资料"]]);
    }
    try {
      const data = await GovernanceApp.api("/api/search", { query: q, description, types: ["news", "policy", "paper"], regionPreference: "domestic-first", timeRange: evidenceTimeRange(`${q} ${description}`), maxResults: 28 }, { signal });
      const liveRows = (data.results || []).filter((row) => row.sourceVerified || row.sourceUsable);
      const picked = chooseSources(liveRows);
      state.sources = mergeSources(state.sources, picked);
      state.topic = q; GovernanceApp.saveTopic(q); GovernanceApp.saveEvidence(state.sources); renderSources();
      if (!silent) { finishComposeProgress(state.sources.length ? `证据整理完成，共 ${state.sources.length} 条资料。` : "当前未检索到可用佐证材料。"); GovernanceApp.toast(state.sources.length ? `当前共引用 ${state.sources.length} 条资料` : "当前未检索到可用佐证材料，可调整主题后重试"); }
      return state.sources;
    } catch (error) {
      if (!silent) { hideComposeProgress(); GovernanceApp.toast(error.name === "AbortError" ? "已停止资料补全" : (error.message || "资料补全失败")); }
      if (silent) throw error; return [];
    } finally { if (!silent) { state.controller = null; setGenerating(false); } }
  }

  function articleBlocks(article) {
    if (Array.isArray(article.blocks) && article.blocks.length) return article.blocks;
    const blocks = [];
    for (const part of String(article.markdown || "").split(/\n\n+/)) {
      const line = part.trim(); if (!line) continue;
      if (/^##\s+/.test(line)) blocks.push({ type: "heading", text: line.replace(/^##\s+/, "") });
      else blocks.push({ type: "paragraph", text: line });
    }
    return blocks;
  }

  function imageFigure(block, cls = "article-image") {
    if (!block?.url) return "";
    const caption = block.caption || block.description || ""; const proxy = GovernanceApp.previewImageUrl(block.url);
    const sourceUrl = GovernanceApp.safeUrl(block.sourceUrl || "");
    const sourceName = block.source || "图片来源";
    const source = sourceUrl !== "#" ? ` <a href="${sourceUrl}" target="_blank" rel="noopener noreferrer">${GovernanceApp.escapeHtml(sourceName)} ↗</a>` : "";
    return `<figure class="${cls}"><img src="${proxy}" data-original-src="${GovernanceApp.safeUrl(block.url)}" alt="${GovernanceApp.escapeHtml(block.description || "文章配图")}" loading="lazy" referrerpolicy="no-referrer">${caption || source ? `<figcaption>${GovernanceApp.escapeHtml(caption)}${source}</figcaption>` : ""}</figure>`;
  }

  function referencesEnabled(article) {
    return Boolean(article?.generationMeta?.writingSpec?.citations);
  }

  function sourceHtml(article) {
    const sources = article.sourceList || [];
    if (!referencesEnabled(article) || !sources.length) return "";
    return `<section class="preview-sources"><h2>参考来源</h2>${sources.map((src) => {
      const title = GovernanceApp.escapeHtml(src.title || "来源");
      const titleNode = src.url ? `<a href="${GovernanceApp.safeUrl(src.url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : `<strong>${title}</strong>`;
      const badge = src.origin === "upload" ? `<em>上传文件</em>` : "";
      return `<p><span>[${src.n}]</span>${titleNode}<small>${badge}${GovernanceApp.escapeHtml([src.source, src.publishedAt ? GovernanceApp.formatDate(src.publishedAt) : ""].filter(Boolean).join(" · "))}</small></p>`;
    }).join("")}</section>`;
  }

  function renderArticle(article) {
    const title = article.recommendedTitle || article.titleCandidates?.[0] || state.topic;
    let body = ""; let currentHeading = "";
    if (article.coverImage?.url) body += imageFigure({ ...article.coverImage, caption: article.coverImage.caption || article.coverImage.description }, "article-image cover-image");
    articleBlocks(article).forEach((block, index) => {
      if (block.type === "heading") {
        currentHeading = block.text || "";
        body += `<h2 class="editable-block" data-edit-index="${index}" data-edit-heading="${GovernanceApp.escapeHtml(currentHeading)}">${inlineMarkdown(block.text || "")}</h2>`;
      } else if (block.type === "paragraph") {
        body += `<p class="editable-block" data-edit-index="${index}" data-edit-heading="${GovernanceApp.escapeHtml(currentHeading)}">${inlineMarkdown(block.text || "")}</p>`;
      } else if (block.type === "bullets") {
        body += `<ul class="editable-block" data-edit-index="${index}" data-edit-heading="${GovernanceApp.escapeHtml(currentHeading)}">${(block.items || []).map((x) => `<li>${inlineMarkdown(x)}</li>`).join("")}</ul>`;
      } else if (block.type === "image") body += imageFigure(block);
    });
    $("#articlePreview").innerHTML = `<h1>${GovernanceApp.escapeHtml(title)}</h1><div class="article-deck">${GovernanceApp.escapeHtml(article.deck || "")}</div>${body}${sourceHtml(article)}`;
    if (article.articleId) sessionStorage.setItem("deg.articleId", article.articleId);
    state.selectedBlock = null; state.selectedText = ""; state.selectedHeading = ""; updateRevisionTarget();
  }

  function generationAuditHtml(article) {
    const meta = article.generationMeta || {}; const spec = meta.writingSpec || {};
    const ok = meta.apiCalled === true;
    const target = spec.minChars && spec.maxChars ? `${spec.minChars}—${spec.maxChars} 字` : (spec.lengthLabel || "未指定");
    const actual = Number(meta.actualChars || 0);
    const chips = [spec.style, spec.tone, spec.audience, spec.structure, spec.opener, spec.closingMode].filter(Boolean);
    return `<section class="analysis-section"><h3>本次 AI 调用与写作规格</h3>
      <div class="api-audit">
        <div class="${ok ? "good" : ""}"><strong>${ok ? "已真实调用" : "未调用"}</strong><span>${GovernanceApp.escapeHtml(meta.model || "DeepSeek")}</span></div>
        <div><strong>${GovernanceApp.escapeHtml(meta.totalTokens || 0)}</strong><span>总 Token · ${GovernanceApp.escapeHtml(meta.callCount || 0)} 次 API</span></div>
        <div><strong>${GovernanceApp.escapeHtml(actual || "—")}</strong><span>正文实际字数</span></div>
        <div class="${meta.withinTarget ? "good" : ""}"><strong>${GovernanceApp.escapeHtml(target)}</strong><span>${meta.withinTarget ? "已进入所选范围" : "未完全进入所选范围"}</span></div>
      </div>
      <div class="spec-chips">${chips.map((x) => `<span>${GovernanceApp.escapeHtml(x)}</span>`).join("")}</div>
      ${meta.withinTarget === false ? `<div class="length-warning">当前字数没有完全进入用户选择的区间。系统已经自动做过长度校正；可继续用“再次修改”要求扩写或压缩。</div>` : ""}
      <p class="muted">输入 Token ${GovernanceApp.escapeHtml(meta.promptTokens || 0)} · 输出 Token ${GovernanceApp.escapeHtml(meta.completionTokens || 0)}${meta.reasoningTokens ? ` · 推理 Token ${GovernanceApp.escapeHtml(meta.reasoningTokens)}` : ""} · 佐证材料 ${GovernanceApp.escapeHtml(meta.sourceCount || 0)} 条${meta.autoEvidenceAdded ? `（自动补充 ${GovernanceApp.escapeHtml(meta.autoEvidenceAdded)} 条）` : ""}</p>
    </section>`;
  }

  function renderInsights(article) {
    const titles = article.titleCandidates || []; const claims = article.keyClaims || []; const understood = article.understoodBrief || ""; const briefPlan = article.understoodBriefPlan || {};
    const risks = article.riskNotes?.length ? article.riskNotes : ["模型没有主动标记高风险项，但发布前仍建议核对关键数字、政策名称和机构表述。"];
    const notes = article.editorialNotes || [];
    $("#insightPanel").innerHTML = `${generationAuditHtml(article)}${understood ? `<section class="analysis-section"><h3>系统对写作要求的理解</h3><div class="analysis-line"><strong>${GovernanceApp.escapeHtml(understood)}</strong><span>这段只用于展示理解结果，不会进入文章正文。</span></div>${briefPlan.mustInclude?.length || briefPlan.avoid?.length ? `<div class="understanding-grid"><span>重点落实：${GovernanceApp.escapeHtml((briefPlan.mustInclude||[]).slice(0,3).join("；")||"按主题判断")}</span><span>明确避免：${GovernanceApp.escapeHtml((briefPlan.avoid||[]).slice(0,3).join("；")||"无")}</span></div>` : ""}</section>` : ""}<section class="analysis-section"><h3>标题候选</h3><div class="title-options">${titles.map((t, i) => `<button data-title="${GovernanceApp.escapeHtml(t)}" class="${i === 0 ? "active" : ""}">${GovernanceApp.escapeHtml(t)}</button>`).join("")}</div></section>
      ${notes.length ? `<section class="analysis-section"><h3>深度编辑做了什么</h3>${notes.map((n) => `<div class="risk-line">${GovernanceApp.escapeHtml(n)}</div>`).join("")}</section>` : ""}
      <section class="analysis-section"><h3>核心判断</h3>${claims.length ? claims.map((c) => `<div class="analysis-line"><strong>${GovernanceApp.escapeHtml(c.claim || "")}</strong><span>置信度 ${GovernanceApp.escapeHtml(c.confidence || "—")} · 来源 ${(c.sourceIds || []).map((n) => `[${n}]`).join(" ")}</span></div>`).join("") : `<p class="muted">本次结果没有返回结构化核心判断。</p>`}</section>
      <section class="analysis-section"><h3>事实与风险提醒</h3>${risks.map((r) => `<div class="risk-line">${GovernanceApp.escapeHtml(r)}</div>`).join("")}</section>`;

    const visuals = article.visuals || []; const sources = article.sourceList || []; const vr = article.visualReport || {};
    const strategyLabels = { smart: "智能混合", real_first: "真实图片优先", diagram_first: "解释图优先", all_diagram: "全部代码绘图", real_only: "仅真实图片" };
    const strategyLabel = strategyLabels[vr.strategy || article.generationMeta?.writingSpec?.imageStrategy] || "智能混合";
    const realCount = Number(vr.realPlaced || 0); const diagramCount = Number(vr.generatedDiagram || 0);
    const providerText = article.visualStatus === "pending"
      ? `正在执行${strategyLabel}`
      : `封面 ${Number(vr.coverPlaced || 0)} + 正文 ${Number(vr.bodyPlaced || 0)} · 真实图 ${realCount} · 代码图 ${diagramCount}`;
    const visualRows = visuals.map((v, i) => {
      const preview = GovernanceApp.previewImageUrl(v.image?.url || "");
      const sourceUrl = GovernanceApp.safeUrl(v.image?.sourceUrl);
      const unresolved = String(v.matchedBy || "").startsWith("unresolved") || !v.image;
      let provider = "";
      if (unresolved) provider = "未命中可靠图片";
      else if (v.image?.provider === "source-origin" || v.image?.provider === "source-meta") provider = "源新闻/源材料页面图";
      else if (v.image?.provider === "serper") provider = "Serper / Google Images";
      else if (v.image?.provider === "generated-diagram") provider = `系统代码绘图${v.image?.generatedKind ? ` · ${v.image.generatedKind}` : ""}`;
      else if (v.kind === "cover" || v.image?.provider === "generated-cover") provider = "系统代码封面";
      else provider = v.image?.provider || "";
      const sourceBinding = v.sourceId ? ` · 优先绑定来源 [${GovernanceApp.escapeHtml(v.sourceId)}]` : "";
      const reason = v.image?.generationReason ? ` · 绘图原因 ${GovernanceApp.escapeHtml(v.image.generationReason)}` : "";
      return `<div class="visual-audit-row"><span>${String(i + 1).padStart(2, "0")}</span><div class="visual-audit-content">${preview !== "#" ? `<img class="visual-audit-thumb" src="${preview}" alt="${GovernanceApp.escapeHtml(v.image?.description || v.purpose || "文章配图")}" loading="lazy">` : ""}<div><strong>${GovernanceApp.escapeHtml(v.kind === "cover" ? "文章封面" : `置于「${v.afterHeading || "正文"}」之后`)}</strong><p>${GovernanceApp.escapeHtml(v.purpose || "语义配图")}</p><small>${provider ? `${GovernanceApp.escapeHtml(provider)} · ` : ""}${GovernanceApp.escapeHtml(v.query || "")}${sourceBinding}${reason}${v.image?.source ? ` · ${GovernanceApp.escapeHtml(v.image.source)}` : ""}${v.image?.matchScore ? ` · 匹配 ${GovernanceApp.escapeHtml(v.image.matchScore)}` : ""}${v.image?.width ? ` · ${GovernanceApp.escapeHtml(v.image.width)}×${GovernanceApp.escapeHtml(v.image.height)}` : ""}${sourceUrl !== "#" ? ` · <a href="${sourceUrl}" target="_blank" rel="noopener noreferrer">图片来源 ↗</a>` : ""}</small></div></div></div>`;
    }).join("");
    const fallbackNote = vr.fallback ? `<div class="length-warning">仍有 ${GovernanceApp.escapeHtml(vr.fallback)} 个图片位没有完成。${(vr.strategy === "real_only") ? "当前是‘仅真实图片’，因此系统不会用代码图补足。" : "系统已尝试来源原图、网络图片和代码解释图；建议查看该位置的内容是否本身不适合配图。"}</div>` : "";
    $("#visualPanel").innerHTML = `<section class="analysis-section"><h3>自动配图计划 · ${GovernanceApp.escapeHtml(providerText)}</h3><div class="spec-chips"><span>策略：${GovernanceApp.escapeHtml(strategyLabel)}</span><span>真实图：${GovernanceApp.escapeHtml(realCount)}</span><span>代码图：${GovernanceApp.escapeHtml(diagramCount)}</span></div>${fallbackNote}${visualRows || `<p class="muted">本次未返回配图计划。</p>`}</section>
      <section class="analysis-section"><h3>参考来源 · ${sources.length}</h3>${sources.map((s) => `<div class="source-audit-row"><span>[${s.n}]</span><div>${s.url ? `<a href="${GovernanceApp.safeUrl(s.url)}" target="_blank" rel="noopener noreferrer">${GovernanceApp.escapeHtml(s.title)}</a>` : `<strong>${GovernanceApp.escapeHtml(s.title)}</strong>`}<small>${s.origin === "upload" ? "上传文件 · " : ""}${GovernanceApp.escapeHtml(s.source || "来源")}${s.publishedAt ? ` · ${GovernanceApp.formatDate(s.publishedAt)}` : ""}${s.sourceImages?.length ? ` · 已提取 ${GovernanceApp.escapeHtml(s.sourceImages.length)} 张来源页图片候选` : ""}</small></div></div>`).join("")}</section>`;
  }

  function enableActions(enabled, visualReady = true) { ["#reviseButton", "#copyButton", "#htmlButton", "#mdButton"].forEach((id) => $(id).disabled = !enabled); ["#docxButton", "#pdfButton"].forEach((id) => $(id).disabled = !enabled || !visualReady); }

  function syncAutoEvidenceFromArticle(article) {
    const rows = (article?.sourceList || []).filter((src) => src && src.origin !== "upload");
    if (!rows.length) return;
    const normalized = rows.map((src, i) => ({
      ...src, id: src.id || `generated-source-${src.n || i + 1}`, origin: src.origin || "auto", autoEvidenceSelected: true,
    }));
    state.sources = mergeSources(state.sources, normalized);
    GovernanceApp.saveEvidence(state.sources);
    renderSources();
  }

  async function generate() {
    if (state.generating) return; const topic = $("#topicInput").value.trim(); if (!topic) return GovernanceApp.toast("请输入文章主题");
    state.topic = topic; GovernanceApp.saveTopic(topic); state.controller = new AbortController(); setGenerating(true);
    setComposeProgress(4, "正在检查主题、证据和写作参数…");
    scheduleComposeStages([
      [650, 14, state.sources.length ? "正在整理已引用的证据资料…" : ($("#autoEvidenceToggle").checked ? "证据为空：正在自动检索佐证材料。" : "已关闭自动佐证：本次只按主题和用户要求写作。")],
      [2400, 30, "正在搭文章骨架：哪些该先说，哪些适合后说。"],
      [4700, 50, "DeepSeek 正在写首稿，同时把事实和用户要求对齐。"],
      [6200, 72, "正在做本地质量检查：标题、分点、篇幅和 AI 套话。"],
      [7600, 88, "文章已经可以先看；配图在后台独立匹配，避免让你干等。"],
    ]);
    try {
      const autoEvidence = $("#autoEvidenceToggle").checked;
      if (!state.sources.length && autoEvidence) {
        try {
          await autoCollect({ silent: true, signal: state.controller.signal });
          setComposeProgress(25, state.sources.length ? `已整理 ${state.sources.length} 条证据，开始写作。` : "前端检索暂未命中，生成端会继续自动补充资料。");
        } catch (e) {
          if (e?.name === "AbortError") throw e;
          setComposeProgress(25, "前端资料检索暂时失败，生成端将继续执行自动补充，不中断写作。");
        }
      }
      if (!state.sources.length && !autoEvidence) setComposeProgress(25, "不自动添加佐证材料，直接按当前主题与写作规格调用 DeepSeek。");
      const article = await GovernanceApp.api("/api/generate", {
        query: topic, description: $("#angleInput").value.trim(), sources: state.sources,
        options: {
          style: $("#styleSelect").value, audience: $("#audienceSelect").value, length: $("#lengthSelect").value,
          angle: $("#angleInput").value.trim(), tone: $("#toneSelect").value, titleMode: $("#titleMode").value,
          structure: $("#structureSelect").value, closingMode: $("#closingMode").value, opener: $("#openerSelect").value,
          paragraphRhythm: $("#rhythmSelect").value, evidenceStyle: $("#evidenceStyle").value, qualityMode: $("#qualityMode").value,
          aiClicheGuard: $("#clicheToggle").checked, smartSections: $("#smartSectionsToggle").checked, autoEvidence, bodyImageCount: getBodyImageCount(), imagePreference: $("#imagePreference").value,
          imageStrategy: $("#imageStrategy")?.value || "smart", imageMatchMode: $("#imageMatchMode").value, imageSourcePolicy: $("#imageSourcePolicy").value, citations: $("#citationToggle").checked, factCheck: $("#factToggle").checked,
        },
      }, { signal: state.controller.signal });
      state.article = article; syncAutoEvidenceFromArticle(article); renderArticle(article); renderInsights(article); enableActions(true, article.visualStatus !== "pending"); $("#undoRevision").disabled = !(article.historyDepth > 0); switchTab("preview");
      finishComposeProgress(article.visualStatus === "pending" ? "正文已经生成；封面和正文配图正在后台匹配，不耽误你先读文章。" : "图文稿已经整理完成。", "文章完成");
      const gm = article.generationMeta || {}; GovernanceApp.toast(article.warnings?.[0] || `DeepSeek 已调用 ${gm.callCount || 0} 次：正文约 ${gm.actualChars || "—"} 字 · ${gm.totalTokens || 0} Token`);
      watchVisualJob(article.articleId);
    } catch (error) {
      hideComposeProgress();
      if (error.name === "AbortError") { GovernanceApp.toast("已停止本次生成"); if (!state.article) $("#articlePreview").innerHTML = `<div class="article-empty"><div class="empty-face">(－_－) zzZ</div><strong>本次生成已停止</strong><p>左侧设置都还保留着，改完可以继续。</p></div>`; }
      else { GovernanceApp.toast(error.message || "生成失败"); if (!state.article) $("#articlePreview").innerHTML = `<div class="article-empty"><div class="empty-face">(╥﹏╥)</div><strong>这次没写成</strong><p>${GovernanceApp.escapeHtml(error.message || "服务暂时没有返回完整文章，请稍后重试。")}</p></div>`; }
    } finally { state.controller = null; setGenerating(false); }
  }

  let visualPollTimer = null;
  function watchVisualJob(articleId) {
    clearTimeout(visualPollTimer);
    if (!articleId) return;
    const poll = async () => {
      try {
        const res = await fetch(`/api/article/${encodeURIComponent(articleId)}`, { cache: "no-store" });
        if (!res.ok) return;
        const latest = await res.json();
        if (state.article?.articleId !== articleId) return;
        state.article = latest;
        renderArticle(latest); renderInsights(latest);
        const status = latest.visualStatus || "ready";
        if (status === "pending") {
          setComposeProgress(92, "正在按所选策略匹配源图、网络图或本地解释图；正文已经可以正常阅读。", "正在配图");
          visualPollTimer = setTimeout(poll, 1200);
        } else {
          enableActions(true, status !== "pending");
          if (status === "ready") {
            finishComposeProgress(`配图完成：封面 ${latest.visualReport?.coverPlaced || 0} 张，正文 ${latest.visualReport?.bodyPlaced || 0} 张。`, "图文稿完成");
            GovernanceApp.toast(`配图完成：封面 ${latest.visualReport?.coverPlaced || 0} + 正文 ${latest.visualReport?.bodyPlaced || 0}`);
          } else if (status === "error") {
            finishComposeProgress("文章已完成，但配图服务暂未完成。", "文章完成");
            GovernanceApp.toast("文章已完成；部分配图处理失败，可在“配图与来源”里查看具体位置");
          }
        }
      } catch {}
    };
    poll();
  }

  function switchTab(name) { $$('[data-tab]').forEach((b) => b.classList.toggle("active", b.dataset.tab === name)); $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`)); }
  function selectTitle(title) { if (!state.article) return; state.article.recommendedTitle = title; state.article.titleCandidates = [title, ...(state.article.titleCandidates || []).filter((x) => x !== title)]; renderArticle(state.article); renderInsights(state.article); GovernanceApp.toast("已切换主标题，导出会使用当前标题"); }

  function updateRevisionTarget() {
    const label = $("#revisionTargetLabel"); const selection = $("#revisionSelection"); if (!label || !selection) return;
    if (state.selectedText) { label.textContent = "已选择一句 / 一段文字"; selection.textContent = `当前划词：${state.selectedText.slice(0, 180)}${state.selectedText.length > 180 ? "…" : ""}`; $("#revisionScope").value = "sentence"; return; }
    if (state.selectedBlock) { label.textContent = state.selectedHeading ? `已选「${state.selectedHeading}」中的内容` : "已选择正文段落"; selection.textContent = `当前段落：${String(state.selectedBlock.text || (state.selectedBlock.items || []).join("；")).slice(0, 220)}`; return; }
    label.textContent = "未选局部内容，可修改整篇"; selection.textContent = "提示：点击正文段落即可选中；要改一句话，先在正文里用鼠标划选文字。";
  }

  function openRevision() { if (!state.article) return; $("#revisionPanel").hidden = false; switchTab("preview"); updateRevisionTarget(); $("#revisionInstruction").focus(); }

  function selectEditableBlock(el) {
    $$("#articlePreview .editable-block").forEach((x) => x.classList.remove("edit-selected")); el.classList.add("edit-selected");
    const blocks = articleBlocks(state.article); const block = blocks[Number(el.dataset.editIndex)]; state.selectedBlock = block || null; state.selectedHeading = el.dataset.editHeading || (block?.type === "heading" ? block.text : ""); state.selectedText = ""; updateRevisionTarget();
  }

  function captureTextSelection() {
    const sel = window.getSelection(); const text = String(sel?.toString() || "").trim(); if (!text || text.length < 2 || !$("#articlePreview").contains(sel.anchorNode)) return;
    const el = sel.anchorNode.nodeType === 1 ? sel.anchorNode.closest?.(".editable-block") : sel.anchorNode.parentElement?.closest(".editable-block");
    if (el) selectEditableBlock(el); state.selectedText = text.slice(0, 6000); updateRevisionTarget();
  }

  async function applyRevision() {
    if (!state.article?.articleId || state.generating) return;
    const instruction = $("#revisionInstruction").value.trim(); if (!instruction) return GovernanceApp.toast("请先写修改要求");
    const scope = $("#revisionScope").value;
    let targetText = ""; let targetHeading = state.selectedHeading || "";
    if (scope === "sentence") targetText = state.selectedText || state.selectedBlock?.text || "";
    else if (scope === "paragraph") targetText = state.selectedBlock?.text || (state.selectedBlock?.items || []).join("；");
    else if (scope === "section" && !targetHeading) return GovernanceApp.toast("先点击要修改章节里的任意一段");
    if ((scope === "sentence" || scope === "paragraph") && !targetText) return GovernanceApp.toast("先点击段落或划选要修改的文字");

    state.controller = new AbortController(); setGenerating(true); setComposeProgress(8, "正在读取你的修改要求…", "正在改稿");
    scheduleComposeStages([[900, 28, "正在定位你指定的句子 / 段落，别的地方先不乱动。", "正在改稿"], [2600, 55, "DeepSeek 正在按要求重写，同时守住原来的事实边界。", "正在改稿"], [5200, 78, "正在检查上下文衔接和来源编号有没有被改乱。", "正在改稿"], [7600, 91, $("#refreshImagesToggle").checked ? "正在重新核对配图位置。" : "保留原配图，正在重新整理文章结构。", "正在改稿"]]);
    try {
      const article = await GovernanceApp.api("/api/revise", { articleId: state.article.articleId, scope, targetText, targetHeading, instruction, refreshImages: $("#refreshImagesToggle").checked }, { signal: state.controller.signal });
      state.article = article; renderArticle(article); renderInsights(article); $("#revisionInstruction").value = ""; $("#undoRevision").disabled = !(article.historyDepth > 0); finishComposeProgress(article.revisionSummary || "修改完成。"); GovernanceApp.toast("已按要求完成修改，可以继续改或直接导出");
    } catch (e) { hideComposeProgress(); GovernanceApp.toast(e.name === "AbortError" ? "已停止本次修改" : (e.message || "修改失败")); }
    finally { state.controller = null; setGenerating(false); }
  }

  async function revisionAction(path, successText) {
    if (!state.article?.articleId || state.generating) return;
    try {
      const article = await GovernanceApp.api(path, { articleId: state.article.articleId }); state.article = article; renderArticle(article); renderInsights(article); $("#undoRevision").disabled = !(article.historyDepth > 0); GovernanceApp.toast(successText);
    } catch (e) { GovernanceApp.toast(e.message || "操作失败"); }
  }

  function createWechatHtml() {
    const article = state.article; if (!article) return "";
    const title = article.recommendedTitle || article.titleCandidates?.[0] || state.topic;
    const pStyle = "margin:16px 0;color:#1c1c1c;font-family:SimSun,'Songti SC',STSong,serif;font-size:15px;line-height:2;text-align:justify;text-indent:2em;";
    const hStyle = "margin:34px 0 14px;color:#111827;font-family:SimHei,'Heiti SC','PingFang SC',sans-serif;font-size:21px;line-height:1.5;font-weight:700;";
    let body = "";
    const addImage = (block) => {
      const caption = GovernanceApp.escapeHtml(block.caption || block.description || "");
      const sourceUrl = GovernanceApp.safeUrl(block.sourceUrl || "");
      const sourceName = GovernanceApp.escapeHtml(block.source || "图片来源");
      const source = sourceUrl !== "#" ? ` <a href="${sourceUrl}" style="color:#23618e;text-decoration:none;">${sourceName} ↗</a>` : (block.source ? ` · 图片来源：${sourceName}` : "");
      body += `<section style="margin:26px 0;"><img src="${GovernanceApp.safeUrl(block.url)}" alt="${GovernanceApp.escapeHtml(block.description || "文章配图")}" style="display:block;width:100%;height:auto;margin:0;"/>${caption || source ? `<p style="margin:7px 0 0;color:#8196a8;font-family:SimSun,'Songti SC',STSong,serif;font-size:11px;line-height:1.6;text-align:center;text-indent:0;">${caption}${source}</p>` : ""}</section>`;
    };
    if (article.coverImage?.url) addImage(article.coverImage);
    for (const block of articleBlocks(article)) {
      if (block.type === "heading") body += `<h2 style="${hStyle}">${inlineMarkdown(block.text || "")}</h2>`;
      else if (block.type === "paragraph") body += `<p style="${pStyle}">${inlineMarkdown(block.text || "")}</p>`;
      else if (block.type === "bullets") body += `<ul style="padding-left:1.3em;color:#2e4559;font-family:SimSun,'Songti SC',STSong,serif;font-size:15px;line-height:2;">${(block.items || []).map((x) => `<li>${inlineMarkdown(x)}</li>`).join("")}</ul>`;
      else if (block.type === "image" && block.url) addImage(block);
    }
    const sourceSection = referencesEnabled(article) && (article.sourceList || []).length ? `<section style="margin-top:42px;padding-top:18px;border-top:1px solid #dce8f2;"><h2 style="${hStyle}">参考来源</h2>${article.sourceList.map((s) => `<p style="margin:10px 0;color:#61788b;font-family:SimSun,'Songti SC',STSong,serif;font-size:12px;line-height:1.7;text-indent:0;">[${s.n}] ${s.url ? `<a href="${GovernanceApp.safeUrl(s.url)}" style="color:#23618e;text-decoration:none;">${GovernanceApp.escapeHtml(s.title || "来源")}</a>` : GovernanceApp.escapeHtml(s.title || "来源")}${s.source ? ` · ${GovernanceApp.escapeHtml(s.source)}` : ""}</p>`).join("")}</section>` : "";
    return `<section style="max-width:720px;margin:0 auto;color:#111827;"><h1 style="margin:0 0 16px;font-family:SimHei,'Heiti SC','PingFang SC',sans-serif;font-size:30px;line-height:1.4;font-weight:700;">${GovernanceApp.escapeHtml(title)}</h1><p style="margin:0 0 30px;padding-bottom:22px;border-bottom:1px solid #dce8f2;color:#60778a;font-family:SimSun,'Songti SC',STSong,serif;font-size:15px;line-height:1.8;">${GovernanceApp.escapeHtml(article.deck || "")}</p>${body}${sourceSection}</section>`;
  }

  async function copyWechat() { const html = createWechatHtml(); if (!html) return; try { if (navigator.clipboard && window.ClipboardItem) await navigator.clipboard.write([new ClipboardItem({ "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([state.article.markdown || ""], { type: "text/plain" }) })]); else await navigator.clipboard.writeText(html); GovernanceApp.toast("已复制完整公众号富文本"); } catch { await navigator.clipboard.writeText(state.article.markdown || html); GovernanceApp.toast("已复制文章文本"); } }
  function triggerDownload(blob, filename) { const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1200); }
  async function exportFile(format) {
    if (!state.article?.articleId) return;
    const label = format === "docx" ? "Word" : "PDF";
    const title = state.article.recommendedTitle || state.article.titleCandidates?.[0] || state.topic;
    try {
      const response = await fetch(`/api/export?articleId=${encodeURIComponent(state.article.articleId)}&format=${encodeURIComponent(format)}&title=${encodeURIComponent(title)}`, { cache: "no-store" });
      if (!response.ok) { const e = await response.json().catch(() => ({})); throw new Error(e.error || e.detail || `${label} 导出失败`); }
      const blob = await response.blob();
      if (!blob.size) throw new Error(`${label} 导出文件为空`);
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const filename = match ? decodeURIComponent(match[1]) : `${title.replace(/[\\/:*?"<>|]/g, "-").slice(0, 48)}.${format}`;
      triggerDownload(blob, filename); GovernanceApp.toast(`${label} 已生成并下载`);
    } catch (e) { GovernanceApp.toast(e.message || `${label} 导出失败`); }
  }

  function downloadHtml() {
    if (!state.article) return;
    const title = state.article.recommendedTitle || state.article.titleCandidates?.[0] || state.topic;
    const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${GovernanceApp.escapeHtml(title)}</title></head><body>${createWechatHtml()}</body></html>`;
    triggerDownload(new Blob([html], { type: "text/html;charset=utf-8" }), `${title.replace(/[\/:*?"<>|]/g, "-").slice(0, 48)}.html`);
  }

  function downloadMarkdown() {
    if (!state.article) return;
    const title = state.article.recommendedTitle || state.article.titleCandidates?.[0] || state.topic;
    const visuals = (state.article.visuals || []).map((v) => `${v.kind === "cover" ? "封面" : v.afterHeading}：${v.image?.url || ""}`).join("\n");
    const sourceText = referencesEnabled(state.article) ? (state.article.sourceList || []).map((s) => `[${s.n}] ${s.title}${s.url ? ` — ${s.url}` : ""}`).join("\n") : "";
    const visualSection = visuals ? `\n\n---\n\n## 自动配图\n\n${visuals}` : "";
    const sourceSection = sourceText ? `\n\n## 参考来源\n\n${sourceText}` : "";
    const content = `# ${title}\n\n> ${state.article.deck || ""}\n\n${state.article.markdown || ""}${visualSection}${sourceSection}\n`;
    triggerDownload(new Blob([content], { type: "text/markdown;charset=utf-8" }), `${title.replace(/[\/:*?"<>|]/g, "-").slice(0, 48)}.md`);
  }

  async function loadProviderStatus() {
    const el = $("#imageProviderStatus");
    try {
      state.health = await GovernanceApp.getHealth();
      if (state.health.serperImagesConfigured) {
        el.className = "image-provider-status ready"; el.innerHTML = `<span class="mini-spinner"></span><div><strong>混合配图已就绪：源新闻 + Serper + 本地代码绘图</strong><small>真实案例优先回到来源页面找原图；结构化分析可直接画；找不到可靠真实图时再绘制解释图。</small></div>`;
      } else {
        el.className = "image-provider-status ready"; el.innerHTML = `<span class="mini-spinner"></span><div><strong>本地代码视觉引擎已就绪 · Serper 未配置</strong><small>仍可使用智能混合 / 解释图优先 / 全部代码绘图；若希望补充真实网络图片，再在 .env 配置 SERPER_API_KEY。</small></div>`;
      }
      const llm = $("#generationApiStatus");
      if (state.health.deepseekConfigured) {
        llm.className = "generation-api-status ready";
        llm.innerHTML = `<span class="mini-spinner"></span><div><strong>DeepSeek 已连接 · ${GovernanceApp.escapeHtml(state.health.deepseekModel || "已配置模型")}</strong><small>本版本只接受真实 API 返回；生成后“生成分析”会展示 API 调用次数和 Token 用量。</small></div>`;
        $("#generateButton").disabled = false;
      } else {
        llm.className = "generation-api-status warn";
        llm.innerHTML = `<span class="mini-spinner"></span><div><strong>DeepSeek API 未配置，AI 写作已停用</strong><small>请在 .env 填 DEEPSEEK_API_KEY 并重启。不会再用本地模板伪装生成。</small></div>`;
        $("#generateButton").disabled = true;
        $("#generateButton").title = "请先配置 DEEPSEEK_API_KEY";
        GovernanceApp.toast("DeepSeek API 未配置：已禁用生成按钮，不会用模板冒充 AI");
      }
    } catch {
      el.className = "image-provider-status warn"; el.querySelector("strong").textContent = "图片服务状态读取失败";
      const llm = $("#generationApiStatus"); if (llm) { llm.className = "generation-api-status warn"; llm.querySelector("strong").textContent = "DeepSeek 服务状态读取失败"; }
    }
  }

  async function restoreDraftIfAvailable() {
    const articleId = sessionStorage.getItem("deg.articleId");
    if (!articleId) return;
    try {
      const response = await fetch(`/api/article/${encodeURIComponent(articleId)}`, { cache: "no-store" });
      if (!response.ok) { sessionStorage.removeItem("deg.articleId"); return; }
      const article = await response.json();
      state.article = article;
      state.topic = state.topic || GovernanceApp.loadTopic() || "数据要素";
      renderArticle(article); renderInsights(article); enableActions(true, article.visualStatus !== "pending"); switchTab("preview");
      if (article.visualStatus === "pending") watchVisualJob(article.articleId);
      GovernanceApp.toast("已恢复上次草稿");
    } catch {
      sessionStorage.removeItem("deg.articleId");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    state.sources = GovernanceApp.loadEvidence(); state.topic = GovernanceApp.loadTopic() || "数据要素"; $("#topicInput").value = state.topic; renderSources(); initComposeColumnSync(); syncImageStrategyControls(); loadProviderStatus(); restoreDraftIfAvailable();
    $("#sourceList").addEventListener("click", (e) => { const b = e.target.closest("[data-remove-source]"); if (!b) return; state.sources = state.sources.filter((s) => s.id !== b.dataset.removeSource); GovernanceApp.saveEvidence(state.sources); renderSources(); });
    const zone = $("#uploadZone"); zone.addEventListener("click", () => $("#fileInput").click()); zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") $("#fileInput").click(); });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragging"); }); zone.addEventListener("dragleave", () => zone.classList.remove("dragging")); zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("dragging"); uploadFiles(e.dataTransfer.files); }); $("#fileInput").addEventListener("change", (e) => uploadFiles(e.target.files));
    $("#autoSourceButton").addEventListener("click", () => autoCollect()); $("#generateButton").addEventListener("click", generate); $("#cancelGenerateButton").addEventListener("click", () => state.controller?.abort());
    $$('[data-tab]').forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab))); $("#insightPanel").addEventListener("click", (e) => { const b = e.target.closest("[data-title]"); if (b) selectTitle(b.dataset.title); });
    $("#articlePreview").addEventListener("click", (e) => { const el = e.target.closest(".editable-block"); if (el) { selectEditableBlock(el); if (!$("#revisionPanel").hidden) updateRevisionTarget(); } }); $("#articlePreview").addEventListener("mouseup", () => setTimeout(captureTextSelection, 0));
    $("#reviseButton").addEventListener("click", openRevision); $("#closeRevision").addEventListener("click", () => $("#revisionPanel").hidden = true); $("#applyRevision").addEventListener("click", applyRevision); $("#undoRevision").addEventListener("click", () => revisionAction("/api/article/undo", "已撤回上一次修改")); $("#restoreOriginal").addEventListener("click", () => revisionAction("/api/article/restore", "已恢复到初稿；刚才的版本仍可通过撤回找回"));
    $("#imageCount")?.addEventListener("change", (e) => { const custom = e.target.value === "custom"; const input = $("#customImageCount"); if (input) input.hidden = !custom; });
    $("#imageStrategy")?.addEventListener("change", syncImageStrategyControls);
    $("#copyButton").addEventListener("click", copyWechat); $("#htmlButton").addEventListener("click", downloadHtml); $("#docxButton").addEventListener("click", () => exportFile("docx")); $("#pdfButton").addEventListener("click", () => exportFile("pdf")); $("#mdButton").addEventListener("click", downloadMarkdown);
  });
})();
