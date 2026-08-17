(() => {
  const state = { items: [], type: "all" };
  const $ = GovernanceApp.$;
  function canOpen(item) { return (item.sourceVerified || item.sourceUsable) && GovernanceApp.safeUrl(item.url) !== "#"; }
  function render() {
    const rows = state.type === "all" ? state.items : state.items.filter((x) => x.type === state.type);
    $("#feedStatus").textContent = `${rows.length} 条推荐`;
    if (!rows.length) {
      $("#feedAllList").innerHTML = `<div class="blank-results"><div class="empty-mark">暂无</div><strong>当前分类暂无推荐</strong><p>可切换分类，或前往查询页按主题检索。</p></div>`;
      return;
    }
    $("#feedAllList").innerHTML = rows.map((item) => {
      const url = GovernanceApp.safeUrl(item.url);
      const title = GovernanceApp.escapeHtml(item.title || "未命名资料");
      const titleNode = canOpen(item) ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${title}</a>` : `<span>${title}</span>`;
      const action = canOpen(item) ? `<a href="${url}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>` : `<span class="source-unavailable">原文地址不可用</span>`;
      const sourceBadge = item.sourceVerified ? `<span class="verified-badge">已核验原文</span>` : `<span class="indexed-badge">已定位原文</span>`;
      const regionBadge = item.originRegion === "global" ? `<span class="region-badge region-global">国际补充</span>` : `<span class="region-badge">国内</span>`;
      return `<article class="feed-all-row">
        <div class="feed-all-meta"><span class="source-badge type-${GovernanceApp.escapeHtml(item.type)}">${GovernanceApp.typeLabel(item.type)}</span>${regionBadge}${sourceBadge}<span>${GovernanceApp.escapeHtml(item.source || "来源")}</span><span>${GovernanceApp.formatDate(item.publishedAt)}</span></div>
        <h2>${titleNode}</h2><p>${GovernanceApp.escapeHtml(item.snippet || "暂无摘要")}</p>
        <div class="feed-all-actions"><span>综合价值 ${item.score || "—"}</span>${action}<a href="/search.html?q=${encodeURIComponent(item.title || "数据要素")}">围绕此主题查询 →</a></div>
      </article>`;
    }).join("");
  }
  async function load() {
    try {
      const response = await fetch("/api/home-feed", { cache: "no-store" });
      if (!response.ok) throw new Error(`推荐请求失败 (${response.status})`);
      const data = await response.json(); state.items = data.items || []; render();
    } catch (e) {
      $("#feedStatus").textContent = "推荐正在更新";
      $("#feedAllList").innerHTML = `<div class="blank-results failure"><div class="empty-mark">更新中</div><strong>推荐内容暂未完成更新</strong><p>可直接前往查询页，按主题检索最新资料。</p></div>`;
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    $("#feedFilters").addEventListener("click", (e) => { const b = e.target.closest("[data-type]"); if (!b) return; state.type = b.dataset.type; [...$("#feedFilters").querySelectorAll("button")].forEach((x) => x.classList.toggle("active", x === b)); render(); });
    load();
  });
})();
