const state = {
  results: [],
  evidence: new Map(),
  images: [],
  article: null,
  query: "数字要素",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const els = {
  query: $("#queryInput"),
  search: $("#searchButton"),
  timeRange: $("#timeRange"),
  sortMode: $("#sortMode"),
  resultList: $("#resultList"),
  resultCount: $("#resultCount"),
  summary: $("#researchSummary"),
  evidenceCount: $("#evidenceCount"),
  evidenceList: $("#evidenceList"),
  clearEvidence: $("#clearEvidence"),
  visualTrack: $("#visualTrack"),
  generate: $("#generateButton"),
  articlePreview: $("#articlePreview"),
  copy: $("#copyButton"),
  downloadMd: $("#downloadMdButton"),
  downloadDocx: $("#downloadDocxButton"),
  downloadPdf: $("#downloadPdfButton"),
  auditPanel: $("#auditPanel"),
  titleCandidates: $("#titleCandidates"),
  riskNotes: $("#riskNotes"),
  imagePlan: $("#imagePlan"),
  loadingLayer: $("#loadingLayer"),
  loadingLabel: $("#loadingLabel"),
  toast: $("#toast"),
  statusButton: $("#statusButton"),
  statusText: $("#statusText"),
  statusDot: $(".status-dot"),
};

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value = "") {
  const url = String(value || "").trim();
  if (/^(https?:|data:image\/)/i.test(url)) return url;
  return "#";
}

function previewImageUrl(value = "") {
  const url = safeUrl(value);
  if (url.startsWith("data:image/")) return url;
  if (url.startsWith("http://") || url.startsWith("https://")) {
    return `/api/image?url=${encodeURIComponent(url)}`;
  }
  return url;
}

function formatDate(value) {
  if (!value) return "时间未标注";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

function typeLabel(type) {
  return ({ news: "新闻", paper: "论文", policy: "政策", web: "网页" })[type] || "资料";
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 2400);
}

function setLoading(on, label = "处理中") {
  els.loadingLabel.textContent = label;
  els.loadingLayer.hidden = !on;
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `请求失败 (${response.status})`);
  return data;
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const data = await response.json();
    if (data.tavilyConfigured && data.deepseekConfigured) {
      els.statusText.textContent = "实时服务已连接";
      els.statusDot.classList.add("good");
    } else {
      els.statusText.textContent = "演示模式";
      els.statusDot.classList.add("demo");
    }
    els.statusButton.title = `Tavily: ${data.tavilyConfigured ? "已配置" : "未配置"}\nDeepSeek: ${data.deepseekConfigured ? data.deepseekModel : "未配置"}\n导出: Word / PDF`;
  } catch {
    els.statusText.textContent = "服务未连接";
  }
}

function selectedTypes() {
  const values = $$(".type-switch input:checked").map((input) => input.value);
  return values.length ? values : ["news", "paper", "policy"];
}

async function runSearch() {
  const query = els.query.value.trim();
  if (!query) return showToast("请输入研究主题");
  state.query = query;
  state.article = null;
  disableExportButtons();
  setLoading(true, "正在跨来源研究");
  try {
    const data = await api("/api/search", {
      query,
      types: selectedTypes(),
      timeRange: els.timeRange.value,
      maxResults: 15,
    });
    state.results = data.results || [];
    state.images = data.images || [];
    renderResearchSummary(data);
    sortAndRender();
    renderVisuals();
    if (state.evidence.size === 0) autoSelectEvidence();
    if (data.warnings?.length) showToast(data.warnings[0]);
    document.querySelector(".research-shell").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showToast(error.message || "检索失败");
  } finally {
    setLoading(false);
  }
}

function renderResearchSummary(data) {
  const meta = data.meta || {};
  const mode = data.demo ? "演示结果" : "实时结果";
  els.summary.innerHTML = `
    <p class="eyebrow">研究摘要 · ${escapeHtml(mode)}</p>
    <p>${escapeHtml(data.answer || `围绕“${state.query}”获得 ${meta.count || state.results.length} 条资料。`)}</p>
  `;
  const parts = [];
  if (meta.policies) parts.push(`${meta.policies} 政策`);
  if (meta.news) parts.push(`${meta.news} 新闻`);
  if (meta.papers) parts.push(`${meta.papers} 论文`);
  els.resultCount.textContent = `${state.results.length} 条来源${parts.length ? " · " + parts.join(" / ") : ""}`;
}

function sortedResults() {
  const items = [...state.results];
  const mode = els.sortMode.value;
  if (mode === "freshness") return items.sort((a, b) => (b.freshnessScore || 0) - (a.freshnessScore || 0));
  if (mode === "authority") return items.sort((a, b) => (b.authorityScore || 0) - (a.authorityScore || 0));
  return items.sort((a, b) => (b.score || 0) - (a.score || 0));
}

function sortAndRender() {
  const rows = sortedResults();
  if (!rows.length) {
    els.resultList.innerHTML = `<div class="empty-state"><span>00</span><p>没有找到结果。</p><small>换一个更具体的表达，或扩大时间范围。</small></div>`;
    return;
  }
  els.resultList.innerHTML = rows.map((item, index) => {
    const selected = state.evidence.has(item.id);
    const authorText = item.authors?.length ? ` · ${escapeHtml(item.authors.slice(0, 3).join("、"))}` : "";
    const cites = item.type === "paper" && Number.isFinite(item.citations) ? ` · 引用 ${item.citations}` : "";
    return `
      <article class="result-row" data-id="${escapeHtml(item.id)}">
        <div class="result-index">${String(index + 1).padStart(2, "0")}</div>
        <div class="result-main">
          <h3><a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
          <div class="result-meta">
            <span class="type-mark">${typeLabel(item.type)}</span>
            <span>${escapeHtml(item.source || "来源")}${authorText}</span>
            <span>${formatDate(item.publishedAt)}${cites}</span>
            <span>权威 ${item.authorityScore || "—"}</span>
          </div>
          <p>${escapeHtml(item.snippet || "暂无摘要")}</p>
        </div>
        <div class="result-side">
          <div class="score"><strong>${item.score || 0}</strong><small>VALUE</small></div>
          <button class="evidence-action ${selected ? "selected" : ""}" data-evidence="${escapeHtml(item.id)}">${selected ? "已加入 ✓" : "+ 加入证据"}</button>
        </div>
      </article>`;
  }).join("");
}

function autoSelectEvidence() {
  const picked = [];
  for (const type of ["policy", "news", "paper"]) {
    const hit = state.results.find((item) => item.type === type && !picked.includes(item));
    if (hit) picked.push(hit);
  }
  for (const item of sortedResults()) {
    if (picked.length >= 5) break;
    if (!picked.includes(item)) picked.push(item);
  }
  picked.forEach((item) => state.evidence.set(item.id, item));
  renderEvidence();
  sortAndRender();
  if (picked.length) showToast(`已按来源类型自动挑选 ${picked.length} 条证据，可继续调整`);
}

function toggleEvidence(id) {
  const item = state.results.find((row) => row.id === id);
  if (!item) return;
  if (state.evidence.has(id)) state.evidence.delete(id);
  else state.evidence.set(id, item);
  renderEvidence();
  sortAndRender();
}

function renderEvidence() {
  const items = [...state.evidence.values()];
  els.evidenceCount.textContent = items.length;
  if (!items.length) {
    els.evidenceList.innerHTML = `<p class="rail-intro">还没有选择资料。</p>`;
    return;
  }
  els.evidenceList.innerHTML = items.map((item, index) => `
    <div class="evidence-item">
      <p>${String(index + 1).padStart(2, "0")} · ${escapeHtml(item.title)}</p>
      <div><span>${typeLabel(item.type)} · ${escapeHtml(item.source || "来源")}</span><button data-remove="${escapeHtml(item.id)}" aria-label="移除">×</button></div>
    </div>
  `).join("");
}

function renderVisuals(images = state.images) {
  const chosen = (images || []).filter(Boolean).slice(0, 3);
  if (!chosen.length) return;
  els.visualTrack.innerHTML = chosen.map((image, index) => `
    <figure class="visual-item">
      <img src="${previewImageUrl(image.url)}" data-original-src="${safeUrl(image.url)}" alt="${escapeHtml(image.description || `${state.query} 配图`)}" loading="lazy" referrerpolicy="no-referrer" />
      <figcaption class="visual-caption">${String(index + 1).padStart(2, "0")} · ${escapeHtml(image.description || "主题相关视觉")}${image.source ? ` · ${escapeHtml(image.source)}` : ""}</figcaption>
    </figure>
  `).join("");
}

async function generateArticle() {
  const sources = [...state.evidence.values()];
  if (!sources.length) return showToast("请至少加入一条证据");
  setLoading(true, "正在写作并自动完成图文编排");
  try {
    const data = await api("/api/generate", {
      query: state.query || els.query.value.trim() || "数字要素",
      sources,
      options: {
        style: $("#styleSelect").value,
        audience: $("#audienceSelect").value,
        length: $("#lengthSelect").value,
        citations: $("#citationToggle").checked,
        factCheck: $("#factToggle").checked,
      },
    });
    state.article = data;
    state.images = (data.visuals || []).map((v) => v.image).filter(Boolean);
    if (!state.images.length && data.images?.length) state.images = data.images;
    renderVisuals(state.images);
    renderArticle(data);
    renderAudit(data);
    enableExportButtons();
    if (data.warnings?.length) showToast(data.warnings[0]);
    else showToast(`已自动插入 ${data.visualReport?.placed || state.images.length} 张图片`);
    els.articlePreview.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showToast(error.message || "生成失败");
  } finally {
    setLoading(false);
  }
}

function inlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d+)\]/g, '<span class="source-ref">[$1]</span>');
}

function markdownBlocks(markdown) {
  const lines = String(markdown || "").replace(/\r/g, "").split("\n");
  const blocks = [];
  let para = [];
  let list = [];
  const flushPara = () => {
    if (para.length) {
      blocks.push({ type: "paragraph", text: para.join(" ") });
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push({ type: "bullets", items: list });
      list = [];
    }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    if (line.startsWith("### ")) { flushPara(); flushList(); blocks.push({ type: "heading", level: 3, text: line.slice(4) }); continue; }
    if (line.startsWith("## ")) { flushPara(); flushList(); blocks.push({ type: "heading", level: 2, text: line.slice(3) }); continue; }
    if (line.startsWith("# ")) { flushPara(); flushList(); blocks.push({ type: "heading", level: 1, text: line.slice(2) }); continue; }
    if (/^[-*]\s+/.test(line)) { flushPara(); list.push(line.replace(/^[-*]\s+/, "")); continue; }
    para.push(line);
  }
  flushPara(); flushList();
  return blocks;
}

function articleBlocks(article) {
  return Array.isArray(article.blocks) && article.blocks.length ? article.blocks : markdownBlocks(article.markdown);
}

function imageFigure(block, className = "article-image") {
  const caption = block.caption || block.description || "自动匹配的文章配图";
  const sourceLink = block.sourceUrl && /^https?:/i.test(block.sourceUrl)
    ? ` <a href="${safeUrl(block.sourceUrl)}" target="_blank" rel="noopener noreferrer">查看来源</a>`
    : "";
  return `<figure class="${className}">
    <img src="${previewImageUrl(block.url)}" data-original-src="${safeUrl(block.url)}" alt="${escapeHtml(block.description || "文章配图")}" referrerpolicy="no-referrer"/>
    <figcaption>${escapeHtml(caption)}${sourceLink}</figcaption>
  </figure>`;
}

function sourceSectionHtml(article) {
  const sources = article.sourceList || [];
  if (!sources.length) return "";
  return `<section class="preview-sources"><h2>参考来源</h2>${sources.map((src) => `<p><span class="source-ref">[${src.n}]</span> <a href="${safeUrl(src.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(src.title || "来源")}</a><small>${escapeHtml([src.source, src.publishedAt ? formatDate(src.publishedAt) : ""].filter(Boolean).join(" · "))}</small></p>`).join("")}</section>`;
}

function renderArticle(article) {
  const title = article.titleCandidates?.[0] || state.query;
  const blocks = articleBlocks(article);
  let body = "";
  if (article.coverImage?.url) {
    body += imageFigure({ ...article.coverImage, caption: article.coverImage.caption || article.coverImage.description }, "article-image cover-image");
  }
  for (const block of blocks) {
    if (block.type === "heading") body += `<h2>${inlineMarkdown(block.text || "")}</h2>`;
    if (block.type === "paragraph") body += `<p>${inlineMarkdown(block.text || "")}</p>`;
    if (block.type === "bullets") body += `<ul>${(block.items || []).map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`;
    if (block.type === "image" && block.url) body += imageFigure(block);
  }
  body += sourceSectionHtml(article);
  els.articlePreview.innerHTML = `<h1>${escapeHtml(title)}</h1><div class="deck">${escapeHtml(article.deck || "")}</div>${body}`;
}

function renderAudit(article) {
  els.auditPanel.hidden = false;
  els.titleCandidates.innerHTML = (article.titleCandidates || []).map((title) => `<button class="audit-title-btn" data-title="${escapeHtml(title)}">${escapeHtml(title)}</button>`).join("");
  const risks = article.riskNotes?.length ? article.riskNotes : ["未发现明显风险项，但正式发布前仍建议核对关键事实与图片使用权限。"];
  els.riskNotes.innerHTML = risks.map((risk) => `<p>— ${escapeHtml(risk)}</p>`).join("");
  const visuals = article.visuals || [];
  els.imagePlan.innerHTML = visuals.map((visual, index) => {
    const place = visual.kind === "cover" ? "封面" : `置于「${visual.afterHeading || "正文"}」之后`;
    const source = visual.image?.source ? ` · ${visual.image.source}` : "";
    return `<p><strong>${String(index + 1).padStart(2, "0")} · ${escapeHtml(place)}</strong><br/>${escapeHtml(visual.purpose || "语义配图")}<br/><span class="audit-muted">${escapeHtml(visual.query || "")}${escapeHtml(source)}</span></p>`;
  }).join("");
}

function selectTitle(title) {
  if (!state.article) return;
  const first = state.article.titleCandidates || [];
  state.article.titleCandidates = [title, ...first.filter((x) => x !== title)];
  renderArticle(state.article);
  showToast("已切换主标题；导出将使用当前标题");
}

function createWechatHtml() {
  if (!state.article) return "";
  const article = state.article;
  const title = article.titleCandidates?.[0] || state.query;
  const blocks = articleBlocks(article);
  let content = "";
  const pStyle = "margin:16px 0;color:#344c5f;font-size:15px;line-height:2;text-align:justify;";
  const hStyle = "margin:38px 0 14px;color:#15283b;font-size:21px;line-height:1.5;font-weight:700;";
  const imgStyle = "display:block;width:100%;height:auto;margin:0;";
  const captionStyle = "margin:7px 0 0;color:#8499a8;font-size:11px;line-height:1.6;text-align:center;";

  const appendImage = (block) => {
    const caption = escapeHtml(block.caption || block.description || "");
    content += `<section style="margin:26px 0;"><img src="${safeUrl(block.url)}" alt="${escapeHtml(block.description || "文章配图")}" style="${imgStyle}"/>${caption ? `<p style="${captionStyle}">${caption}</p>` : ""}</section>`;
  };

  if (article.coverImage?.url) appendImage(article.coverImage);
  for (const block of blocks) {
    if (block.type === "heading") content += `<h2 style="${hStyle}">${inlineMarkdown(block.text || "")}</h2>`;
    else if (block.type === "paragraph") content += `<p style="${pStyle}">${inlineMarkdown(block.text || "")}</p>`;
    else if (block.type === "bullets") content += `<ul style="padding-left:1.3em;color:#344c5f;font-size:15px;line-height:2;">${(block.items || []).map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`;
    else if (block.type === "image" && block.url) appendImage(block);
  }
  const sourceHtml = (article.sourceList || []).length
    ? `<section style="margin-top:42px;padding-top:18px;border-top:1px solid #dceaf4;"><h2 style="${hStyle}">参考来源</h2>${article.sourceList.map((src) => `<p style="margin:10px 0;color:#60778a;font-size:12px;line-height:1.7;">[${src.n}] <a href="${safeUrl(src.url)}" style="color:#285d84;text-decoration:none;">${escapeHtml(src.title || "来源")}</a>${src.source ? ` · ${escapeHtml(src.source)}` : ""}</p>`).join("")}</section>`
    : "";
  return `<section style="max-width:720px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#15283b;"><h1 style="margin:0 0 16px;font-size:30px;line-height:1.35;font-weight:700;">${escapeHtml(title)}</h1><p style="margin:0 0 30px;padding:0 0 22px;border-bottom:1px solid #dceaf4;color:#60778a;font-size:15px;line-height:1.8;">${escapeHtml(article.deck || "")}</p>${content}${sourceHtml}</section>`;
}

async function copyWechat() {
  const html = createWechatHtml();
  if (!html) return;
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      const item = new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([state.article.markdown || ""], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
    } else {
      await navigator.clipboard.writeText(html);
    }
    showToast("已复制包含自动配图的公众号 HTML");
  } catch {
    await navigator.clipboard.writeText(state.article.markdown || html);
    showToast("浏览器不支持富文本复制，已复制 Markdown");
  }
}

async function downloadExport(format) {
  if (!state.article?.articleId) return showToast("请先生成文章");
  const label = format === "docx" ? "Word" : "PDF";
  setLoading(true, `正在生成 ${label}，并嵌入正文图片`);
  try {
    const response = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ articleId: state.article.articleId, format, title: state.article.titleCandidates?.[0] || state.query }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || error.error || `${label} 导出失败`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const fallback = `${(state.article.titleCandidates?.[0] || state.query).replace(/[\\/:*?"<>|]/g, "-").slice(0, 48)}.${format}`;
    const filename = match ? decodeURIComponent(match[1]) : fallback;
    triggerBlobDownload(blob, filename);
    showToast(`${label} 已导出，正文图片已嵌入`);
  } catch (error) {
    showToast(error.message || `${label} 导出失败`);
  } finally {
    setLoading(false);
  }
}

function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadMarkdown() {
  if (!state.article) return;
  const title = state.article.titleCandidates?.[0] || state.query;
  const sourceList = [...state.evidence.values()].map((s, i) => `[${i + 1}] ${s.title} — ${s.url}`).join("\n");
  const visualList = (state.article.visuals || []).map((v) => `${v.kind === "cover" ? "封面" : v.afterHeading}: ${v.image?.url || ""}`).join("\n");
  const content = `# ${title}\n\n> ${state.article.deck || ""}\n\n${state.article.markdown || ""}\n\n---\n\n## 自动配图\n\n${visualList}\n\n## 来源\n\n${sourceList}\n`;
  triggerBlobDownload(new Blob([content], { type: "text/markdown;charset=utf-8" }), `${title.replace(/[\\/:*?"<>|]/g, "-").slice(0, 48)}.md`);
}

function enableExportButtons() {
  for (const button of [els.copy, els.downloadMd, els.downloadDocx, els.downloadPdf]) if (button) button.disabled = false;
}

function disableExportButtons() {
  for (const button of [els.copy, els.downloadMd, els.downloadDocx, els.downloadPdf]) if (button) button.disabled = true;
}

els.search.addEventListener("click", runSearch);
els.query.addEventListener("keydown", (event) => { if (event.key === "Enter") runSearch(); });
els.sortMode.addEventListener("change", sortAndRender);
els.resultList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-evidence]");
  if (button) toggleEvidence(button.dataset.evidence);
});
els.evidenceList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (button) toggleEvidence(button.dataset.remove);
});
els.clearEvidence.addEventListener("click", () => {
  state.evidence.clear(); renderEvidence(); sortAndRender(); showToast("证据篮已清空");
});
els.generate.addEventListener("click", generateArticle);
els.copy.addEventListener("click", copyWechat);
els.downloadMd.addEventListener("click", downloadMarkdown);
els.downloadDocx.addEventListener("click", () => downloadExport("docx"));
els.downloadPdf.addEventListener("click", () => downloadExport("pdf"));
els.titleCandidates.addEventListener("click", (event) => {
  const button = event.target.closest("[data-title]");
  if (button) selectTitle(button.dataset.title);
});
$("#presetRow").addEventListener("click", (event) => {
  const button = event.target.closest("[data-query]");
  if (!button) return;
  els.query.value = button.dataset.query;
  runSearch();
});
els.statusButton.addEventListener("click", () => showToast(els.statusButton.title || "服务状态检测中"));

document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement)) return;
  const original = image.dataset.originalSrc;
  if (original && image.src !== original) {
    image.removeAttribute("data-original-src");
    image.src = original;
  }
}, true);

const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) if (entry.isIntersecting) entry.target.classList.add("visible");
}, { threshold: .08 });
$$('.reveal').forEach((el) => observer.observe(el));

window.addEventListener("scroll", () => {
  $(".topbar").classList.toggle("scrolled", window.scrollY > 20);
  const y = Math.min(window.scrollY * .035, 24);
  document.documentElement.style.setProperty("--ambient-shift", `${y}px`);
}, { passive: true });

renderEvidence();
checkHealth();
