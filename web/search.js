(() => {
  const state = { results: [], selected: new Map(), query: "", answer: "", understanding: null, controller: null, searching: false, demo: false, meta: {} };
  const progress = GovernanceApp.sailingProgress("search");
  const $ = GovernanceApp.$;
  const $$ = GovernanceApp.$$;

  function selectedTypes() { return $$("input[name='sourceType']:checked").map((x) => x.value); }
  function detailId(id) { return `detail-${String(id || "row").replace(/[^a-zA-Z0-9_-]/g, "_")}`; }
  function parseTime(value) { const n = Date.parse(value || ""); return Number.isFinite(n) ? n : 0; }

  function filteredRows() {
    let rows = [...state.results];
    const type = $("#resultTypeFilter").value;
    if (type !== "all") rows = rows.filter((r) => r.type === type);
    const needle = $("#resultKeyword").value.trim().toLowerCase();
    if (needle) rows = rows.filter((r) => `${r.title || ""} ${r.snippet || ""} ${r.source || ""} ${(r.authors || []).join(" ")}`.toLowerCase().includes(needle));
    if ($("#openAccessOnly").checked) rows = rows.filter((r) => r.type !== "paper" || r.openAccess);
    if ($("#abstractOnly").checked) rows = rows.filter((r) => String(r.snippet || "").trim().length > 20);
    const min = Number($("#minScore").value || 0);
    rows = rows.filter((r) => Number(r.score || 0) >= min);

    const sort = $("#resultSort").value;
    const direction = $("#sortDirection").value === "asc" ? 1 : -1;
    const value = (r) => {
      if (sort === "time") return parseTime(r.publishedAt);
      if (sort === "impact") return Number(r.impactCount ?? r.citations ?? r.readCount ?? 0);
      if (sort === "authority") return Number(r.authorityScore || 0);
      return Number(r.score || 0);
    };
    rows.sort((a, b) => direction * (value(a) - value(b)) || Number(b.score || 0) - Number(a.score || 0));
    return rows;
  }

  function scoreBand(score) {
    const n = Number(score || 0);
    if (n >= 85) return "高";
    if (n >= 70) return "中高";
    if (n >= 55) return "中";
    return "一般";
  }

  function blankState(kind = "initial", message = "") {
    if (kind === "failure") return `<div class="blank-results failure"><div class="empty-face">(╥﹏╥)</div><strong>本次查询未完成</strong><p>${GovernanceApp.escapeHtml(message || "请保留当前条件后重试，或缩小查询来源范围。")}</p><button type="button" class="inline-retry" data-retry-search>重新查询</button></div>`;
    if (kind === "stopped") return `<div class="blank-results"><div class="empty-face">(－_－) zzZ</div><strong>本次查询已停止</strong><p>查询条件已保留，可修改后重新检索。</p></div>`;
    if (kind === "none") return `<div class="blank-results"><div class="empty-face">(｡•́︿•̀｡)</div><strong>未找到高度匹配的资料</strong><p>可扩大时间范围、减少限制条件，或换成更具体的事件 / 机构 / 政策关键词。</p></div>`;
    return `<div class="blank-results initial-state"><div class="empty-face">( •̀ ω •́ )✧</div><strong>输入主题，开始检索</strong><p>默认优先中国政策、权威媒体、产业实践和中文论文。</p></div>`;
  }

  function sourceBadge(item) {
    if (item.sourceVerified) return `<span class="verified-badge">已核验原文</span>`;
    if (item.sourceStatus === "indexed" || item.sourceUsable) return `<span class="indexed-badge">已定位原文</span>`;
    return "";
  }

  function impactText(item) {
    if (item.citations !== null && item.citations !== undefined) return `引用 ${Number(item.citations || 0).toLocaleString("zh-CN")}`;
    if (item.readCount !== null && item.readCount !== undefined) return `阅读 ${Number(item.readCount || 0).toLocaleString("zh-CN")}`;
    return "阅读量：来源未提供";
  }

  function renderResults() {
    const rows = filteredRows();
    if (!state.results.length) return;
    $("#resultTitle").textContent = `${rows.length} 条结果`;
    const counts = ["news", "policy", "paper"].map((t) => `${GovernanceApp.typeLabel(t)} ${rows.filter((r) => r.type === t).length}`).join(" · ");
    const sortNames = { time: "发布时间", score: "综合匹配", impact: "引用/阅读量", authority: "权威度" };
    const speed = state.meta?.elapsedMs !== undefined ? ` · ${state.meta.cacheHit ? "缓存命中" : `检索 ${(Number(state.meta.elapsedMs) / 1000).toFixed(1)}s`}` : "";
    const region = state.meta?.domesticCount !== undefined ? ` · 国内 ${Number(state.meta.domesticCount || 0)} / 国际 ${Number(state.meta.globalCount || 0)}` : "";
    const sourceQuality = state.meta?.verifiedCount !== undefined ? ` · 原文核验 ${Number(state.meta.verifiedCount || 0)} / 原文定位 ${Number(state.meta.indexedCount || 0)}` : "";
    $("#resultMeta").textContent = `${counts} · ${sortNames[$("#resultSort").value]}${$("#sortDirection").value === "asc" ? "升序" : "降序"}${region}${sourceQuality}${speed}`;
    if (!rows.length) { $("#resultList").innerHTML = blankState("none"); return; }

    $("#resultList").innerHTML = rows.map((item) => {
      const chosen = state.selected.has(item.id);
      const authors = item.authors?.length ? item.authors.slice(0, 3).join("、") : "";
      const dId = detailId(item.id);
      const sourceUrl = GovernanceApp.safeUrl(item.url);
      const pdfUrl = GovernanceApp.safeUrl(item.pdfUrl);
      const canOpen = (item.sourceVerified || item.sourceUsable || item.sourceStatus === "indexed") && sourceUrl !== "#";
      const canOpenPdf = canOpen && pdfUrl !== "#" && pdfUrl !== sourceUrl;
      const titleNode = canOpen ? `<a href="${sourceUrl}" target="_blank" rel="noopener noreferrer">${GovernanceApp.escapeHtml(item.title)}</a>` : `<span>${GovernanceApp.escapeHtml(item.title)}</span>`;
      const sourceAction = canOpen ? `<a href="${sourceUrl}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>` : `<span class="source-unavailable">原文地址不可用</span>`;
      const pdfAction = canOpenPdf ? `<a href="${pdfUrl}" target="_blank" rel="noopener noreferrer">PDF / 全文 ↗</a>` : "";
      const regionLabel = item.originRegion === "global" ? "国际" : "国内";
      return `<article class="research-row ${chosen ? "selected" : ""}" data-id="${GovernanceApp.escapeHtml(item.id)}">
        <button class="row-select" data-select="${GovernanceApp.escapeHtml(item.id)}" aria-label="${chosen ? "取消引用" : "引用资料"}"><span>${chosen ? "✓" : "+"}</span></button>
        <div class="row-content">
          <div class="row-topline"><span class="source-badge type-${GovernanceApp.escapeHtml(item.type)}">${GovernanceApp.typeLabel(item.type)}</span><span class="region-badge region-${GovernanceApp.escapeHtml(item.originRegion || "domestic")}">${regionLabel}</span>${sourceBadge(item)}<span>${GovernanceApp.escapeHtml(item.source || "来源")}</span><span>${GovernanceApp.formatDate(item.publishedAt)}</span>${authors ? `<span>${GovernanceApp.escapeHtml(authors)}</span>` : ""}</div>
          <h2>${titleNode}</h2>
          <p>${GovernanceApp.escapeHtml(item.snippet || "暂无摘要")}</p>
          <div class="row-actions"><button data-detail="${GovernanceApp.escapeHtml(dId)}">查看评分细节</button>${sourceAction}${pdfAction}<span>${GovernanceApp.escapeHtml(impactText(item))}</span></div>
          <div class="row-detail" id="${GovernanceApp.escapeHtml(dId)}" hidden><dl><div><dt>相关度</dt><dd>${Math.round(Number(item.relevance || 0) * 100)}%</dd></div><div><dt>权威度</dt><dd>${item.authorityScore ?? "—"}</dd></div><div><dt>时效性</dt><dd>${item.freshnessScore ?? "—"}</dd></div><div><dt>影响力</dt><dd>${item.impactCount ?? 0}</dd></div><div><dt>为什么匹配</dt><dd>${GovernanceApp.escapeHtml(item.matchReason || "与用户主题存在明确关联")}</dd></div></dl></div>
        </div>
        <div class="row-score"><strong>${item.score || 0}</strong><span>${scoreBand(item.score)}</span></div>
      </article>`;
    }).join("");
  }

  function renderSelection() {
    const rows = [...state.selected.values()];
    $("#selectionCount").textContent = rows.length;
    $("#selectionBar").hidden = rows.length === 0;
    const counts = ["policy", "news", "paper"].map((t) => { const n = rows.filter((r) => r.type === t).length; return n ? `${GovernanceApp.typeLabel(t)} ${n}` : ""; }).filter(Boolean);
    $("#selectionTypes").textContent = counts.length ? `${counts.join(" · ")} · 已引用` : "会带到推文生成页";
    GovernanceApp.saveEvidence(rows.map((r) => ({ ...r, origin: "search", selectedByUser: true })));
  }

  function toggleSelect(id) {
    const item = state.results.find((r) => r.id === id);
    if (!item) return;
    if (state.selected.has(id)) state.selected.delete(id); else state.selected.set(id, { ...item, origin: "search", selectedByUser: true });
    renderResults(); renderSelection();
  }

  function autoPick() {
    state.selected.clear();
    const rows = filteredRows();
    if (!rows.length) return GovernanceApp.toast("当前没有可自动引用的结果");
    const ordered = [...rows].sort((a,b) => Number(Boolean(b.sourceVerified))-Number(Boolean(a.sourceVerified)) || Number(b.score||0)-Number(a.score||0));
    const top = Number(ordered[0]?.score||0); const threshold = Math.max(55, top - 14);
    const picked = [];
    for (const type of ["policy", "news", "paper"]) { const hit = ordered.find((r) => r.type === type && Number(r.score||0) >= threshold); if (hit) picked.push(hit); }
    for (const row of ordered) { if (picked.length >= 8) break; if (!picked.some((x) => x.id === row.id)) picked.push(row); }
    picked.forEach((r) => state.selected.set(r.id, { ...r, origin: "search", selectedByUser: true }));
    renderResults(); renderSelection(); GovernanceApp.toast(`已引用 ${picked.length} 条高价值资料`);
  }

  function renderUnderstanding(understanding = state.understanding) {
    const answer = $("#researchAnswer");
    if (!understanding?.intentSummary) { answer.hidden = true; return; }
    const must = (understanding.mustTerms || []).slice(0, 5).join("、") || "主题语义";
    const avoid = (understanding.excludeTerms || []).slice(0, 5).join("、") || "暂无";
    const regionNames = { "domestic-only": "仅国内", "domestic-first": "国内优先", "domestic+global": "国内外对照", "global-first": "国际优先", "global-only": "仅国际" };
    answer.hidden = false;
    answer.innerHTML = `<strong>检索策略</strong><p>${GovernanceApp.escapeHtml(understanding.intentSummary)}</p><div class="understanding-grid"><span><b>核心主题</b>${GovernanceApp.escapeHtml(must)}</span><span><b>排除内容</b>${GovernanceApp.escapeHtml(avoid)}</span><span><b>地区顺序</b>${GovernanceApp.escapeHtml(regionNames[understanding.regionPreference] || "国内优先")}</span><span><b>来源偏好</b>${GovernanceApp.escapeHtml((understanding.sourcePreference || []).slice(0, 4).join("、") || "政府、权威媒体、学术来源")}</span></div>`;
  }

  function saveSnapshot() {
    try {
      sessionStorage.setItem("deg.searchSnapshot.v20", JSON.stringify({
        savedAt: Date.now(), query: state.query, description: $("#searchDescription").value || "", understanding: state.understanding || null, results: state.results, answer: state.answer, demo: state.demo, meta: state.meta,
        controls: { types: selectedTypes(), regionPreference: $("#regionPreference").value, timeRange: $("#timeRange").value, dateFrom: $("#dateFrom").value, dateTo: $("#dateTo").value, maxResults: $("#maxResults").value }
      }));
    } catch {}
  }

  function restoreSnapshot() {
    try {
      const snap = JSON.parse(sessionStorage.getItem("deg.searchSnapshot.v20") || "null");
      if (!snap || !Array.isArray(snap.results) || Date.now() - Number(snap.savedAt || 0) > 30 * 60 * 1000) return false;
      state.query = snap.query || ""; state.results = (snap.results || []).filter((row) => row && (row.sourceVerified || row.sourceUsable)); state.answer = ""; state.understanding = snap.understanding || null; state.demo = false; state.meta = { ...(snap.meta || {}), cacheHit: true };
      if (!$("#queryInput").value) $("#queryInput").value = state.query;
      if (!$("#searchDescription").value) $("#searchDescription").value = snap.description || "";
      const c = snap.controls || {};
      if (Array.isArray(c.types)) $$('input[name="sourceType"]').forEach((x) => x.checked = c.types.includes(x.value));
      if (c.regionPreference) $("#regionPreference").value = c.regionPreference;
      if (c.timeRange) $("#timeRange").value = c.timeRange;
      $("#customDateRange").hidden = $("#timeRange").value !== "custom";
      if (c.dateFrom) $("#dateFrom").value = c.dateFrom; if (c.dateTo) $("#dateTo").value = c.dateTo; if (c.maxResults) $("#maxResults").value = c.maxResults;
      $("#autoPick").disabled = !state.results.length;
      renderUnderstanding(snap.understanding);
      renderResults();
      return true;
    } catch { return false; }
  }

  function startProgress() {
    progress.set(3, "正在解析主题与附加要求…", "正在查询");
    progress.schedule([
      [350, 16, "优先检索国内政策、权威媒体和产业实践…", "正在查询"],
      [900, 34, "新闻、政策与论文正在并行检索…", "正在查询"],
      [1700, 54, "正在核对原文地址与页面标题…", "正在查询"],
      [2800, 72, "正在合并重复报道与相同来源…", "正在查询"],
      [4200, 86, "正在计算主题匹配、权威度与时效性…", "正在查询"],
      [6000, 94, "正在生成国内优先的结果排序…", "正在查询"],
    ], () => state.searching);
  }
  function finishProgress(text = "查询完成，结果已按综合匹配排序。") { progress.finish(text, "查询完成", 850); }
  function stopProgress() { progress.hide(); }

  function setSearching(on) {
    state.searching = on;
    $("#searchButton").disabled = on;
    $("#searchButton").querySelector("span").textContent = on ? "查询进行中" : "开始查询";
    $("#cancelSearchButton").hidden = !on;
  }

  async function runSearch() {
    if (state.searching) return;
    const q = $("#queryInput").value.trim();
    const description = $("#searchDescription").value.trim();
    if (!q) return GovernanceApp.toast("请输入检索主题");
    const types = selectedTypes();
    if (!types.length) return GovernanceApp.toast("至少选择一种查询来源");
    const timeRange = $("#timeRange").value || "latest";
    if (timeRange === "custom" && !$("#dateFrom").value && !$("#dateTo").value) return GovernanceApp.toast("请至少填写一个自定义日期");
    if (timeRange === "custom" && $("#dateFrom").value && $("#dateTo").value && $("#dateFrom").value > $("#dateTo").value) return GovernanceApp.toast("开始日期不能晚于结束日期");

    state.query = q; GovernanceApp.saveTopic(q); state.controller = new AbortController(); setSearching(true); startProgress();
    $("#resultTitle").textContent = "正在查询"; $("#resultMeta").textContent = "可以随时停止本次查询";
    $("#resultList").innerHTML = `<div class="blank-results"><div class="empty-mark">检索中</div><strong>正在查询相关资料</strong><p>国内权威来源优先，国际资料作为补充。</p></div>`;
    $("#researchAnswer").hidden = true;
    try {
      const data = await GovernanceApp.api("/api/search", { query: q, description, types, regionPreference: $("#regionPreference").value, timeRange, dateFrom: $("#dateFrom").value || null, dateTo: $("#dateTo").value || null, maxResults: Number($("#maxResults").value), searchMode: "fast" }, { signal: state.controller.signal });
      state.results = data.results || []; state.answer = data.answer || ""; state.understanding = data.understanding || null; state.demo = Boolean(data.demo); state.meta = data.meta || {};
      saveSnapshot();
      finishProgress(); $("#autoPick").disabled = !state.results.length;
      renderUnderstanding(data.understanding);
      if (!state.results.length) { $("#resultTitle").textContent = "查询结果"; $("#resultMeta").textContent = "0 条"; $("#resultList").innerHTML = blankState("none"); }
      else renderResults();
      if (data.meta?.partial && state.results.length) GovernanceApp.toast("部分来源响应较慢，已展示当前可用结果");
    } catch (error) {
      stopProgress();
      if (error.name === "AbortError") { $("#resultTitle").textContent = "查询已停止"; $("#resultMeta").textContent = "条件仍保留"; $("#resultList").innerHTML = blankState("stopped"); }
      else { $("#resultTitle").textContent = "查询未完成"; $("#resultMeta").textContent = "查询条件已保留"; $("#resultList").innerHTML = blankState("failure"); GovernanceApp.toast("本次查询未完成，请重试"); }
    } finally { state.controller = null; setSearching(false); }
  }

  function resetFilters() {
    $("#queryInput").value = ""; $("#searchDescription").value = ""; $$("input[name='sourceType']").forEach((x) => x.checked = true);
    $("#regionPreference").value = "domestic-first"; $("#timeRange").value = "latest"; $("#customDateRange").hidden = true; $("#dateFrom").value = ""; $("#dateTo").value = ""; $("#maxResults").value = "20";
    $("#resultTypeFilter").value = "all"; $("#resultKeyword").value = ""; $("#resultSort").value = "score"; $("#sortDirection").value = "desc";
    $("#openAccessOnly").checked = false; $("#abstractOnly").checked = false; $("#minScore").value = "0"; $("#scoreValue").textContent = "0";
    state.results = []; state.answer = ""; state.understanding = null; state.demo = false; state.meta = {}; sessionStorage.removeItem("deg.searchSnapshot.v20"); $("#researchAnswer").hidden = true; $("#resultTitle").textContent = "查询结果"; $("#resultMeta").textContent = "尚未开始查询"; $("#resultList").innerHTML = blankState("initial"); $("#autoPick").disabled = true;
  }

  document.addEventListener("DOMContentLoaded", () => {
    const params = new URLSearchParams(location.search); const incoming = params.get("q") || ""; $("#queryInput").value = incoming;
    if (!incoming) restoreSnapshot();
    GovernanceApp.loadEvidence().forEach((r) => r?.id && state.selected.set(r.id, r)); renderSelection();
    $("#searchButton").addEventListener("click", runSearch); $("#cancelSearchButton").addEventListener("click", () => state.controller?.abort());
    $("#queryInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); runSearch(); } });
    $("#quickTopics").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) $("#queryInput").value = b.dataset.q; });
    $("#timeRange").addEventListener("change", () => { $("#customDateRange").hidden = $("#timeRange").value !== "custom"; });
    $("#resultList").addEventListener("click", (e) => { const retry = e.target.closest("[data-retry-search]"); if (retry) return runSearch(); const select = e.target.closest("[data-select]"); if (select) return toggleSelect(select.dataset.select); const detail = e.target.closest("[data-detail]"); if (detail) { const el = document.getElementById(detail.dataset.detail); if (el) el.hidden = !el.hidden; } });
    $("#autoPick").addEventListener("click", autoPick); $("#clearSelection").addEventListener("click", () => { state.selected.clear(); renderSelection(); if (state.results.length) renderResults(); });
    $("#toCompose").addEventListener("click", () => { GovernanceApp.saveEvidence([...state.selected.values()]); GovernanceApp.saveTopic(state.query || $("#queryInput").value.trim()); location.href = "/compose.html"; });
    $("#resetFilters").addEventListener("click", resetFilters);
    ["#resultTypeFilter", "#resultSort", "#sortDirection", "#openAccessOnly", "#abstractOnly"].forEach((id) => $(id).addEventListener("change", () => state.results.length && renderResults()));
    $("#resultKeyword").addEventListener("input", () => state.results.length && renderResults());
    $("#minScore").addEventListener("input", () => { $("#scoreValue").textContent = $("#minScore").value; if (state.results.length) renderResults(); });
    if (incoming) requestAnimationFrame(runSearch);
  });
})();
