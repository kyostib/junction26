// BS Meter Chrome Extension - Content Script for x.com / twitter.com

(function () {
  "use strict";

  let API_BASE_URL = "http://localhost:8000";
  let enabled = true;

  // Load user settings
  if (chrome && chrome.storage && chrome.storage.sync) {
    chrome.storage.sync.get(["apiBaseUrl", "enabled"], (res) => {
      if (res.apiBaseUrl) API_BASE_URL = res.apiBaseUrl.replace(/\/+$/, "");
      if (typeof res.enabled === "boolean") enabled = res.enabled;
    });
  }

  const pendingQueue = new Map(); // handle -> Set of DOM container elements
  let debounceTimer = null;
  const cache = new Map(); // handle -> UserOverlayResponse

  function extractHandle(element) {
    // 1. Check anchor tags with href matching /username
    const links = element.querySelectorAll('a[href^="/"]');
    for (const link of links) {
      const href = link.getAttribute("href") || "";
      const match = href.match(/^\/([A-Za-z0-9_]{1,15})$/);
      if (match && !["home", "explore", "notifications", "messages", "settings", "search"].includes(match[1].toLowerCase())) {
        return match[1].toLowerCase();
      }
    }

    // 2. Check inner text for @handle
    const text = element.innerText || "";
    const textMatch = text.match(/@([A-Za-z0-9_]{1,15})/);
    if (textMatch) {
      return textMatch[1].toLowerCase();
    }
    return null;
  }

  function renderBadge(container, handle, data) {
    if (!container || !document.contains(container)) return;

    let badge = container.querySelector(`.bs-meter-badge[data-handle="${handle}"]`);
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "bs-meter-badge";
      badge.setAttribute("data-handle", handle);
      container.appendChild(badge);
    }

    const level = data.badge_level || "pending";
    badge.className = `bs-meter-badge bs-badge-${level}`;
    badge.innerText = data.badge_label || (data.status === "ready" ? `BS ${Math.round(data.bs_index || 0)}` : "⏳");

    // Build popover card
    const popover = document.createElement("div");
    popover.className = "bs-meter-popover";

    if (data.status === "ready") {
      const dims = data.dimensions || {};
      const falsehood = dims.falsehood || 0;
      const spam = dims.spam || 0;
      const harm = dims.harm || 0;

      let flagsHtml = "";
      if (data.top_flags && data.top_flags.length > 0) {
        flagsHtml = `
          <div class="bs-flags-section">
            <div style="font-weight:700; margin-bottom:4px; color:#e5e7eb;">Flagged Signals:</div>
            ${data.top_flags
              .slice(0, 2)
              .map(
                (f) => `
                <div class="bs-flag-item">
                  • <b>${f.type.toUpperCase()}:</b> ${f.summary}
                  ${f.evidence_url ? `<a href="${f.evidence_url}" target="_blank" class="bs-evidence-link">Source: ${f.evidence_title || "Context.dev Evidence"}</a>` : ""}
                </div>`
              )
              .join("")}
          </div>
        `;
      }

      popover.innerHTML = `
        <div class="bs-popover-header">
          <span class="bs-popover-title">@${handle}</span>
          <span class="bs-popover-score" style="color: ${level === "low" ? "#10b981" : level === "medium" ? "#f59e0b" : "#f87171"}">
            BS Index: ${Math.round(data.bs_index)}/100
          </span>
        </div>
        <div>
          <div class="bs-metric-row"><span>Falsehoods / Claims:</span><span>${Math.round(falsehood)}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${falsehood}%; background:#38bdf8;"></div></div>

          <div class="bs-metric-row"><span>Spam & Scams:</span><span>${Math.round(spam)}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${spam}%; background:#f59e0b;"></div></div>

          <div class="bs-metric-row"><span>Harm & Toxicity:</span><span>${Math.round(harm)}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${harm}%; background:#f87171;"></div></div>
        </div>
        ${flagsHtml}
        <div class="bs-powered-by">Powered by <b>Context.dev</b></div>
      `;
    } else {
      popover.innerHTML = `
        <div class="bs-popover-header">
          <span class="bs-popover-title">@${handle}</span>
          <span class="bs-popover-score">Analyzing...</span>
        </div>
        <div style="font-size:11px; color:#9ca3af; line-height:1.4;">
          Context.dev is scraping recent tweets, uncloaking links, and verifying claims against web evidence. Results will refresh automatically.
        </div>
        <div class="bs-powered-by">Powered by <b>Context.dev</b></div>
      `;
    }

    // Replace old popover
    const oldPopover = badge.querySelector(".bs-meter-popover");
    if (oldPopover) oldPopover.remove();
    badge.appendChild(popover);
  }

  async function flushBatch() {
    if (pendingQueue.size === 0 || !enabled) return;

    const handlesToFetch = [];
    for (const [handle, containers] of pendingQueue.entries()) {
      if (cache.has(handle)) {
        const cached = cache.get(handle);
        containers.forEach((c) => renderBadge(c, handle, cached));
      } else {
        handlesToFetch.push(handle);
      }
    }

    if (handlesToFetch.length === 0) {
      pendingQueue.clear();
      return;
    }

    const currentBatch = new Map(pendingQueue);
    pendingQueue.clear();

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/overlay/twitter/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usernames: handlesToFetch }),
      });

      if (!response.ok) {
        console.warn("BS Meter batch lookup returned error:", response.status);
        return;
      }

      const data = await response.json();
      const results = data.results || {};

      for (const [handle, info] of Object.entries(results)) {
        cache.set(handle, info);
        const containers = currentBatch.get(handle);
        if (containers) {
          containers.forEach((c) => renderBadge(c, handle, info));
        }
      }
    } catch (err) {
      console.warn("BS Meter overlay API unreachable at", API_BASE_URL, err);
    }
  }

  function scheduleBatch() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(flushBatch, 150);
  }

  function scanDOM() {
    if (!enabled) return;

    // Twitter User-Name containers in tweets
    const targets = document.querySelectorAll('[data-testid="User-Name"], [data-testid="UserName"]');
    targets.forEach((container) => {
      if (container.getAttribute("data-bs-meter-scanned")) return;
      const handle = extractHandle(container);
      if (!handle) return;

      container.setAttribute("data-bs-meter-scanned", "true");

      if (cache.has(handle)) {
        renderBadge(container, handle, cache.get(handle));
      } else {
        if (!pendingQueue.has(handle)) {
          pendingQueue.set(handle, new Set());
        }
        pendingQueue.get(handle).add(container);
        scheduleBatch();
      }
    });
  }

  // Observe Twitter feed changes (infinite scroll)
  const observer = new MutationObserver(() => {
    scanDOM();
  });

  observer.observe(document.body, { childList: true, subtree: true });
  scanDOM();
})();
