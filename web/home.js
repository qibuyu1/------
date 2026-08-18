(() => {
  const state = { items: [], position: 1, logical: 0, timer: null, duration: 6200, paused: false, demo: false };
  const HOME_CACHE_TTL = 7 * 24 * 60 * 60 * 1000;
  const $ = (selector) => document.querySelector(selector);
  function sourceLink(item) {
    const link = GovernanceApp.safeUrl(item.url);
    const title = GovernanceApp.escapeHtml(item.title);
    if (link !== "#" && (item.sourceVerified || item.sourceUsable)) return `<a href="${link}" target="_blank" rel="noopener noreferrer">${title}</a>`;
    return `<span>${title}</span>`;
  }

  function slideHtml(item) {
    const type = GovernanceApp.typeLabel(item.type);
    const meta = [item.source || "来源", item.publishedAt ? GovernanceApp.formatDate(item.publishedAt) : ""].filter(Boolean).join(" · ");
    const sourceBadge = item.sourceVerified ? `<span class="verified-badge">已核验原文</span>` : `<span class="indexed-badge">已定位原文</span>`;
    const regionBadge = item.originRegion === "global" ? `<span class="region-badge region-global">国际补充</span>` : `<span class="region-badge">国内</span>`;
    const searchLink = `/search.html?q=${encodeURIComponent(item.title || "数据要素")}`;
    return `<article class="feed-slide">
      <div class="feed-copy">
        <div class="feed-meta"><span class="feed-type type-${GovernanceApp.escapeHtml(item.type)}">${type}</span>${regionBadge}${sourceBadge}<span>${GovernanceApp.escapeHtml(meta)}</span></div>
        <h2>${sourceLink(item)}</h2>
        <p>${GovernanceApp.escapeHtml(item.snippet || "点击标题可查看原始来源与完整内容。")}</p>
        <div class="feed-bottom"><span>综合价值 ${item.score || "—"}</span><a href="${searchLink}">围绕此主题继续查询 →</a></div>
      </div>
    </article>`;
  }

  function setTrack(position, animate = true) {
    const track = $("#carouselTrack"); if (!track) return;
    track.classList.toggle("animating", animate); state.position = position;
    track.style.transform = `translate3d(-${position * 100}%,0,0)`;
    updateDots();
  }

  function updateDots() {
    const dots = $("#carouselDots"); if (!dots) return;
    [...dots.children].forEach((node, index) => node.classList.toggle("active", index === state.logical));
  }

  function renderDots() {
    const dots = $("#carouselDots"); if (!dots) return;
    dots.innerHTML = state.items.map((_, index) => `<button type="button" data-carousel-index="${index}" aria-label="切换到第 ${index + 1} 条推荐"></button>`).join("");
    updateDots();
  }

  function restartProgress() {
    const line = $("#carouselLine"); if (!line || state.items.length <= 1 || state.paused) return;
    line.style.transition = "none"; line.style.width = "0%";
    requestAnimationFrame(() => requestAnimationFrame(() => { line.style.transition = `width ${state.duration}ms linear`; line.style.width = "100%"; }));
  }

  function goNext() { if (state.items.length <= 1) return; state.logical = (state.logical + 1) % state.items.length; setTrack(state.position + 1, true); restartProgress(); }
  function goPrev() { if (state.items.length <= 1) return; state.logical = (state.logical - 1 + state.items.length) % state.items.length; setTrack(state.position - 1, true); restartProgress(); }
  function goTo(index) {
    if (state.items.length <= 1) return;
    const target = Math.max(0, Math.min(index, state.items.length - 1));
    state.logical = target;
    setTrack(target + 1, true);
    restartProgress();
  }
  function startAuto() { clearInterval(state.timer); state.paused = false; if (state.items.length > 1) state.timer = setInterval(goNext, state.duration); restartProgress(); }
  function pauseAuto() { state.paused = true; clearInterval(state.timer); const line = $("#carouselLine"); if (line) line.style.transition = "none"; }

  function render() {
    const track = $("#carouselTrack"); if (!state.items.length) return;
    if (state.items.length === 1) { track.innerHTML = slideHtml(state.items[0]); track.style.transform = "translate3d(0,0,0)"; renderDots(); return; }
    track.style.transform = "translate3d(0,0,0)";
    const slides = [state.items[state.items.length - 1], ...state.items, state.items[0]];
    track.innerHTML = slides.map(slideHtml).join(""); state.position = 1; state.logical = 0; setTrack(1, false); renderDots();
  }

  function renderEmpty() {
    const viewport = $("#carouselViewport");
    const track = $("#carouselTrack");
    if (track) { track.classList.remove("animating"); track.style.transform = "translate3d(0,0,0)"; }
    const dots = $("#carouselDots");
    if (viewport) viewport.classList.add("empty-feed");
    if ($("#carouselTrack")) $("#carouselTrack").innerHTML = `<article class="feed-slide feed-empty-slide"><div class="feed-copy"><div class="feed-meta"><span class="feed-type">专题入口</span></div><h2>从重点专题开始检索</h2><p>选择一个主题，查看国内政策、权威报道、产业案例和中文论文。</p><div class="feed-topic-links"><a href="/search.html?q=${encodeURIComponent("数据要素治理")}">数据要素治理</a><a href="/search.html?q=${encodeURIComponent("公共数据授权运营")}">公共数据授权运营</a><a href="/search.html?q=${encodeURIComponent("可信数据空间")}">可信数据空间</a><a href="/search.html?q=${encodeURIComponent("数据资产入表")}">数据资产入表</a></div></div></article>`;
    if (dots) dots.innerHTML = "";
  }

  function readCachedFeed() {
    try {
      const cached = JSON.parse(localStorage.getItem("deg.homeFeed.v33") || "null");
      if (!cached || !Array.isArray(cached.items) || !cached.items.length) return null;
      if (Date.now() - Number(cached.savedAt || 0) > HOME_CACHE_TTL) return null;
      return cached.items.filter((item) => item && (item.sourceVerified || item.sourceUsable)).slice(0, 10);
    } catch { return null; }
  }

  async function loadFeed() {
    const cached = readCachedFeed();
    state.items = cached || []; state.demo = false;
    if (state.items.length) {
      render(); startAuto();
      // Homepage recommendations are deliberately weekly, not live. A fresh
      // browser cache must not even call the server, so opening the homepage
      // repeatedly consumes zero Tavily quota for seven days.
      return;
    } else { renderEmpty(); }
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 18000);
      const response = await fetch("/api/home-feed", { cache: "no-store", signal: controller.signal });
      clearTimeout(timeout);
      if (!response.ok) throw new Error(`首页推荐请求失败 ${response.status}`);
      const data = await response.json();
      const items = (data.items || []).slice(0, 10);
      if (!items.length) throw new Error("当前推荐正在更新");
      state.items = items; state.demo = Boolean(data.demo); render(); startAuto();
      if (items.length && !data.demo) localStorage.setItem("deg.homeFeed.v33", JSON.stringify({ items, savedAt: Date.now() }));
    } catch {
      // Keep cached/fallback content visible; a failed refresh should not blank the hero.
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const track = $("#carouselTrack"); const viewport = $("#carouselViewport");
    track.addEventListener("transitionend", () => {
      if (state.items.length <= 1) return;
      if (state.position === state.items.length + 1) setTrack(1, false);
      else if (state.position === 0) setTrack(state.items.length, false);
    });
    viewport.addEventListener("mouseenter", pauseAuto); viewport.addEventListener("mouseleave", startAuto);
    $("#carouselPrev")?.addEventListener("click", () => { pauseAuto(); goPrev(); startAuto(); });
    $("#carouselNext")?.addEventListener("click", () => { pauseAuto(); goNext(); startAuto(); });
    $("#carouselDots")?.addEventListener("click", (e) => {
      const button = e.target.closest("[data-carousel-index]"); if (!button) return;
      pauseAuto(); goTo(Number(button.dataset.carouselIndex)); startAuto();
    });
    let startX = 0;
    viewport.addEventListener("touchstart", (e) => { pauseAuto(); startX = e.touches[0].clientX; }, { passive: true });
    viewport.addEventListener("touchend", (e) => { const delta = e.changedTouches[0].clientX - startX; if (Math.abs(delta) > 45) delta < 0 ? goNext() : goPrev(); startAuto(); }, { passive: true });
    loadFeed();
  });
})();
