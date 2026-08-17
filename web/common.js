(() => {
  const GovernanceApp = {};

  GovernanceApp.$ = (selector, root = document) => root.querySelector(selector);
  GovernanceApp.$$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  GovernanceApp.escapeHtml = (value = "") => String(value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  GovernanceApp.safeUrl = (value = "") => {
    const url = String(value || "").trim();
    if (/^https?:\/\/(?:www\.)?example\.(?:com|org|net)(?:\/|$)/i.test(url)) return "#";
    return (/^(https?:|data:image\/)/i.test(url) || /^\/(?!\/)/.test(url)) ? url : "#";
  };

  GovernanceApp.previewImageUrl = (value = "") => {
    const url = GovernanceApp.safeUrl(value);
    if (/^https?:/i.test(url)) return `/api/image?url=${encodeURIComponent(url)}`;
    return url;
  };

  GovernanceApp.formatDate = (value) => {
    if (!value) return "日期未标注";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  };

  GovernanceApp.typeLabel = (type) => ({ news: "新闻", paper: "论文", policy: "政策", web: "网页", upload: "文件" })[type] || "资料";

  GovernanceApp.api = async (path, payload, options = {}) => {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
      signal: options.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || `请求失败 (${response.status})`);
    return data;
  };

  GovernanceApp.sailingProgress = (prefix) => {
    let timers = [];
    const el = (suffix) => document.querySelector(`#${prefix}${suffix}`);
    const clear = () => { timers.forEach(clearTimeout); timers = []; };
    const set = (percent, text, title = null) => {
      const p = Math.max(0, Math.min(100, Number(percent) || 0));
      const root = el("Progress");
      if (!root) return;
      root.hidden = false;
      if (title && el("ProgressTitle")) el("ProgressTitle").textContent = title;
      if (el("ProgressPercent")) el("ProgressPercent").textContent = `${p}%`;
      if (el("ProgressFill")) el("ProgressFill").style.width = `${p}%`;
      if (el("Boat")) el("Boat").style.left = `calc(${p}% - ${Math.min(18, p * .18)}px)`;
      if (text && el("ProgressText")) el("ProgressText").textContent = text;
    };
    const schedule = (stages, isActive = () => true) => {
      clear();
      stages.forEach(([delay, percent, text, title]) => timers.push(setTimeout(() => {
        if (isActive()) set(percent, text, title || null);
      }, delay)));
    };
    const finish = (text, title = "完成", hideDelay = 900) => {
      clear(); set(100, text, title);
      timers.push(setTimeout(() => { const root = el("Progress"); if (root) root.hidden = true; }, hideDelay));
    };
    const hide = () => { clear(); const root = el("Progress"); if (root) root.hidden = true; };
    return { set, schedule, finish, hide, clear };
  };

  GovernanceApp.toast = (message) => {
    let el = document.querySelector("#globalToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "globalToast";
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(GovernanceApp.toast.timer);
    GovernanceApp.toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
  };

  GovernanceApp.loading = (on, label = "处理中") => {
    let layer = document.querySelector("#globalLoading");
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "globalLoading";
      layer.className = "loading-layer";
      layer.innerHTML = `<div class="loading-panel"><span class="loader"></span><strong></strong><small>请保持当前页面开启</small></div>`;
      document.body.appendChild(layer);
    }
    layer.querySelector("strong").textContent = label;
    layer.hidden = !on;
  };

  GovernanceApp.saveEvidence = (rows) => {
    const clean = (rows || []).filter((row) => row && (row.sourceVerified || row.sourceUsable || row.type === "upload")).slice(0, 12);
    sessionStorage.setItem("deg.evidence", JSON.stringify(clean));
    return clean;
  };

  GovernanceApp.loadEvidence = () => {
    try {
      const rows = JSON.parse(sessionStorage.getItem("deg.evidence") || "[]");
      return Array.isArray(rows) ? rows.filter((row) => row && (row.sourceVerified || row.sourceUsable || row.type === "upload")) : [];
    } catch { return []; }
  };

  GovernanceApp.saveTopic = (query) => sessionStorage.setItem("deg.topic", String(query || "").trim());
  GovernanceApp.loadTopic = () => sessionStorage.getItem("deg.topic") || "数据要素";

  GovernanceApp.getHealth = async () => {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error(`服务状态读取失败 (${response.status})`);
    return response.json();
  };

  GovernanceApp.checkHealth = async () => {
    const status = document.querySelector("#serviceStatus");
    if (!status) return;
    try {
      const data = await GovernanceApp.getHealth();
      const full = data.tavilyConfigured && data.deepseekConfigured && data.serperImagesConfigured;
      const partial = data.tavilyConfigured || data.deepseekConfigured || data.serperImagesConfigured;
      status.classList.toggle("live", full);
      status.querySelector("span:last-child").textContent = full ? "实时服务" : (partial ? "部分服务" : "API 未配置");
      status.title = `Tavily 检索：${data.tavilyConfigured ? "已连接" : "未配置"}\nDeepSeek 写作：${data.deepseekConfigured ? data.deepseekModel : "未配置（无法生成 AI 文章）"}\n图片搜索：${data.serperImagesConfigured ? "Serper / Google Images" : "未配置"}\n导出：Word / PDF`;
    } catch {
      status.querySelector("span:last-child").textContent = "服务未连接";
    }
  };

  GovernanceApp.bindHeader = () => {
    const menu = document.querySelector("#mobileMenuButton");
    const nav = document.querySelector(".site-nav");
    if (menu && nav) menu.addEventListener("click", () => nav.classList.toggle("open"));
    const path = location.pathname.split("/").pop() || "index.html";
    document.querySelectorAll(".site-nav a").forEach((a) => {
      const href = a.getAttribute("href") || "";
      if ((path === "" || path === "index.html") && href.endsWith("index.html")) a.classList.add("active");
      if (path && href.endsWith(path)) a.classList.add("active");
    });
  };

  document.addEventListener("error", (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement)) return;
    const original = image.dataset.originalSrc;
    if (original && image.src !== original) {
      image.removeAttribute("data-original-src");
      image.src = original;
    }
  }, true);

  window.GovernanceApp = GovernanceApp;
  document.addEventListener("DOMContentLoaded", () => {
    GovernanceApp.bindHeader();
    GovernanceApp.checkHealth();
  });
})();
