// BS Meter Chrome Extension - True Floating Overlay for x.com / twitter.com

(function () {
  "use strict";

  console.info(
    "%c[BS Meter] Extension content script LOADED on: " + window.location.href,
    "background: #1d4ed8; color: #ffffff; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 14px;"
  );

  let API_BASE_URL = "http://localhost:8000";
  let enabled = true;
  let currentProfileHandle = null;
  let currentProfileData = null;
  let hideOverlayTimeout = null;
  let isOverlayPinned = false;
  let pollingInterval = null;

  // Reserved paths that are NOT user profiles
  const RESERVED_WORDS = new Set([
    "home", "explore", "notifications", "messages", "settings", "search",
    "i", "compose", "logout", "login", "tos", "privacy", "about", "trends",
    "hashtag", "lists", "bookmarks", "communities", "premium", "verified-orgs-signup"
  ]);

  const PROFILE_SUB_TABS = new Set([
    "with_replies", "highlights", "articles", "media", "likes", "superfollows"
  ]);

  // Load user settings
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
    chrome.storage.sync.get(["apiBaseUrl", "enabled"], (res) => {
      if (res.apiBaseUrl) API_BASE_URL = res.apiBaseUrl.replace(/\/+$/, "");
      if (typeof res.enabled === "boolean") enabled = res.enabled;
      console.info(`[BS Meter] Storage loaded: API_BASE_URL = ${API_BASE_URL}, Enabled = ${enabled}`);
      checkCurrentPage();
    });

    chrome.storage.onChanged.addListener((changes) => {
      if (changes.apiBaseUrl) API_BASE_URL = changes.apiBaseUrl.newValue.replace(/\/+$/, "");
      if (changes.enabled) {
        enabled = changes.enabled.newValue;
        console.info(`[BS Meter] Settings changed: Enabled = ${enabled}`);
        if (!enabled) {
          removeExistingBadges();
          hideOverlay(true);
        } else {
          checkCurrentPage();
        }
      }
    });
  }

  // Ensure singleton global overlay element attached to document.body
  function getOrCreateGlobalOverlay() {
    let overlay = document.getElementById("bs-meter-global-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "bs-meter-global-overlay";
      if (document.body) {
        document.body.appendChild(overlay);
      } else {
        document.addEventListener("DOMContentLoaded", () => document.body.appendChild(overlay));
      }

      overlay.addEventListener("mouseenter", () => {
        if (hideOverlayTimeout) {
          clearTimeout(hideOverlayTimeout);
          hideOverlayTimeout = null;
        }
      });

      overlay.addEventListener("mouseleave", () => {
        if (!isOverlayPinned) {
          hideOverlayTimeout = setTimeout(() => {
            hideOverlay(false);
          }, 250);
        }
      });
    }
    return overlay;
  }

  function positionOverlay(overlay, badge) {
    const rect = badge.getBoundingClientRect();
    const overlayWidth = 330;
    const padding = 16;

    let left = rect.left;
    if (left + overlayWidth > window.innerWidth - padding) {
      left = window.innerWidth - overlayWidth - padding;
    }
    if (left < padding) left = padding;

    let top = rect.bottom + 8;
    const overlayHeight = overlay.offsetHeight || 300;
    if (top + overlayHeight > window.innerHeight - padding && rect.top > overlayHeight + padding) {
      top = rect.top - overlayHeight - 8;
    }

    overlay.style.top = `${Math.round(top)}px`;
    overlay.style.left = `${Math.round(left)}px`;
  }

  function showOverlay(badge, handle, data) {
    if (hideOverlayTimeout) {
      clearTimeout(hideOverlayTimeout);
      hideOverlayTimeout = null;
    }

    const overlay = getOrCreateGlobalOverlay();
    const status = data ? data.status : "queued";
    const level = data && data.badge_level ? data.badge_level : "pending";
    const score = data && typeof data.bs_index === "number" ? Math.round(data.bs_index) : null;

    if (status === "ready" && data) {
      const dims = data.dimensions || {};
      const falsehood = dims.falsehood || 0;
      const spam = dims.spam || 0;
      const harm = dims.harm || 0;

      let flagsHtml = "";
      if (data.top_flags && data.top_flags.length > 0) {
        flagsHtml = `
          <div class="bs-flags-section">
            <div style="font-weight:700; margin-bottom:6px; color:#e5e7eb;">Flagged Signals:</div>
            ${data.top_flags
              .slice(0, 3)
              .map(
                (f) => `
                <div class="bs-flag-item">
                  • <b>${f.type.toUpperCase()}:</b> ${f.summary}
                  ${f.evidence_url ? `<a href="${f.evidence_url}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link">Source: ${f.evidence_title || "Context.dev Evidence"}</a>` : ""}
                </div>`
              )
              .join("")}
          </div>
        `;
      }

      overlay.innerHTML = `
        <div class="bs-popover-header">
          <span class="bs-popover-title">@${handle} - Evidence Report</span>
          <div style="display:flex; align-items:center;">
            <span class="bs-popover-score" style="color: ${level === "low" ? "#10b981" : level === "medium" ? "#f59e0b" : "#f87171"}">
              BS: ${score}/100
            </span>
            <button class="bs-close-btn" id="bsOverlayClose">✕</button>
          </div>
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
        <div class="bs-powered-by">Verified via <b>Context.dev</b></div>
      `;
    } else {
      overlay.innerHTML = `
        <div class="bs-popover-header">
          <span class="bs-popover-title">@${handle}</span>
          <div style="display:flex; align-items:center;">
            <span class="bs-popover-score" style="color:#9ca3af;">Analyzing...</span>
            <button class="bs-close-btn" id="bsOverlayClose">✕</button>
          </div>
        </div>
        <div style="font-size:11px; color:#9ca3af; line-height:1.45;">
          Context.dev is scraping recent tweets, uncloaking links, and verifying claims against authoritative web evidence. Results refresh automatically.
        </div>
        <div class="bs-powered-by">Powered by <b>Context.dev</b></div>
      `;
    }

    const closeBtn = overlay.querySelector("#bsOverlayClose");
    if (closeBtn) {
      closeBtn.onclick = (e) => {
        e.stopPropagation();
        hideOverlay(true);
      };
    }

    overlay.classList.add("visible");
    positionOverlay(overlay, badge);
  }

  function hideOverlay(force = false) {
    if (force) isOverlayPinned = false;
    if (isOverlayPinned && !force) return;

    const overlay = document.getElementById("bs-meter-global-overlay");
    if (overlay) {
      overlay.classList.remove("visible");
    }
  }

  function getProfileHandleFromUrl() {
    const cleanPath = window.location.pathname.split("?")[0].split("#")[0].replace(/^\/+|\/+$/g, "");
    if (!cleanPath) return null;

    const segments = cleanPath.split("/");
    const candidate = segments[0].toLowerCase().trim();

    if (!candidate || RESERVED_WORDS.has(candidate)) {
      return null;
    }

    // Support standard Twitter handles
    if (!/^[a-z0-9_]{1,50}$/.test(candidate)) {
      return null;
    }

    if (segments.length === 1) {
      return candidate;
    } else if (segments.length === 2 && PROFILE_SUB_TABS.has(segments[1].toLowerCase())) {
      return candidate;
    }

    return null;
  }

  function findProfileHeaderElement() {
    const candidateSelectors = [
      '[data-testid="UserName"]',
      '[data-testid="User-Name"]',
      '[data-testid="UserProfileHeader_Items"]',
      'div[data-testid="primaryColumn"] h2',
      'div[data-testid="primaryColumn"] div[dir="ltr"]',
      'main header'
    ];
    for (const selector of candidateSelectors) {
      const el = document.querySelector(selector);
      if (el) return el;
    }
    return null;
  }

  function removeExistingBadges() {
    document.querySelectorAll(".bs-meter-profile-badge").forEach((el) => el.remove());
    hideOverlay(true);
    if (pollingInterval) {
      clearInterval(pollingInterval);
      pollingInterval = null;
    }
  }

  async function fetchProfileBSIndex(handle) {
    console.info(`[BS Meter] Requesting BS Index evaluation for @${handle} from ${API_BASE_URL}...`);

    // 1. Try background worker
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const response = await new Promise((resolve) => {
          chrome.runtime.sendMessage(
            { type: "BATCH_LOOKUP", usernames: [handle], apiUrl: API_BASE_URL },
            (res) => {
              if (chrome.runtime.lastError) {
                console.warn("[BS Meter] Background message error:", chrome.runtime.lastError.message);
                resolve(null);
              } else {
                resolve(res);
              }
            }
          );
        });

        if (response && response.success && response.data && response.data.results) {
          console.info(`[BS Meter] Received results for @${handle} via Background worker:`, response.data.results[handle]);
          return response.data.results[handle];
        }
      } catch (err) {
        console.warn("[BS Meter] Background proxy failed, trying direct fetch:", err);
      }
    }

    // 2. Direct fetch fallback
    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/overlay/twitter/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usernames: [handle] }),
      });
      if (resp.ok) {
        const data = await resp.json();
        console.info(`[BS Meter] Received results for @${handle} via Direct fetch:`, data.results?.[handle]);
        return (data.results || {})[handle];
      }
    } catch (e) {
      console.error("[BS Meter] Direct API fetch error:", e);
    }
    return null;
  }

  function renderProfileBadge(container, handle, data) {
    if (!container || !document.contains(container)) return;

    let badge = container.querySelector(`.bs-meter-profile-badge[data-handle="${handle}"]`);
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "bs-meter-profile-badge";
      badge.setAttribute("data-handle", handle);
      container.appendChild(badge);

      badge.addEventListener("mouseenter", () => {
        showOverlay(badge, handle, badge._bsMeterData);
      });

      badge.addEventListener("mouseleave", () => {
        if (!isOverlayPinned) {
          hideOverlayTimeout = setTimeout(() => {
            hideOverlay(false);
          }, 250);
        }
      });

      badge.addEventListener("click", (e) => {
        e.stopPropagation();
        isOverlayPinned = !isOverlayPinned;
        if (isOverlayPinned) {
          showOverlay(badge, handle, badge._bsMeterData);
        } else {
          hideOverlay(true);
        }
      });

      console.info(`[BS Meter] Attached badge to profile header for @${handle}`);
    }

    badge._bsMeterData = data;
    const status = data ? data.status : "queued";
    const level = data && data.badge_level ? data.badge_level : "pending";
    const score = data && typeof data.bs_index === "number" ? Math.round(data.bs_index) : null;

    let labelText = "⏳ Analyzing Profile via Context.dev...";
    if (status === "ready" && score !== null) {
      if (level === "low") labelText = `✓ BS Index: ${score} (Low BS)`;
      else if (level === "medium") labelText = `⚠️ BS Index: ${score} (Caution)`;
      else labelText = `🚨 BS Index: ${score} (High BS)`;
    }

    badge.className = `bs-meter-profile-badge bs-badge-${level}`;
    badge.innerText = labelText;

    const overlay = document.getElementById("bs-meter-global-overlay");
    if (overlay && overlay.classList.contains("visible")) {
      showOverlay(badge, handle, data);
    }
  }

  function checkCurrentPage() {
    if (!enabled) return;
    const handle = getProfileHandleFromUrl();

    if (!handle) {
      if (currentProfileHandle !== null) {
        console.info(`[BS Meter] Left profile page (@${currentProfileHandle}). Cleaning up.`);
        currentProfileHandle = null;
        currentProfileData = null;
        removeExistingBadges();
      }
      return;
    }

    // Profile page active
    if (currentProfileHandle !== handle) {
      console.info(`%c[BS Meter] Profile detected: @${handle}`, "color: #10b981; font-weight: bold; font-size: 14px;");
      currentProfileHandle = handle;
      currentProfileData = { status: "queued", badge_level: "pending" };
      removeExistingBadges();

      // Trigger background scrape immediately!
      fetchProfileBSIndex(handle).then((data) => {
        if (data && currentProfileHandle === handle) {
          currentProfileData = data;
          const container = findProfileHeaderElement();
          if (container) renderProfileBadge(container, handle, data);

          if (data.status === "queued" || data.status === "analyzing") {
            if (pollingInterval) clearInterval(pollingInterval);
            pollingInterval = setInterval(async () => {
              if (currentProfileHandle !== handle) {
                clearInterval(pollingInterval);
                return;
              }
              const updated = await fetchProfileBSIndex(handle);
              if (updated && updated.status === "ready") {
                clearInterval(pollingInterval);
                currentProfileData = updated;
                const header = findProfileHeaderElement();
                if (header) renderProfileBadge(header, handle, updated);
              }
            }, 4000);
          }
        }
      });
    }

    // Attach badge if header container is rendered
    const headerContainer = findProfileHeaderElement();
    if (headerContainer && !headerContainer.querySelector(`.bs-meter-profile-badge[data-handle="${handle}"]`)) {
      renderProfileBadge(headerContainer, handle, currentProfileData || { status: "queued", badge_level: "pending" });
    }
  }

  // Dismiss pinned overlay on outside click
  document.addEventListener("click", (e) => {
    const overlay = document.getElementById("bs-meter-global-overlay");
    const badge = document.querySelector(".bs-meter-profile-badge");
    if (overlay && overlay.classList.contains("visible")) {
      if (!overlay.contains(e.target) && (!badge || !badge.contains(e.target))) {
        hideOverlay(true);
      }
    }
  });

  // Reposition overlay on window resize or scroll
  window.addEventListener("scroll", () => {
    const overlay = document.getElementById("bs-meter-global-overlay");
    const badge = document.querySelector(".bs-meter-profile-badge");
    if (overlay && overlay.classList.contains("visible") && badge) {
      positionOverlay(overlay, badge);
    }
  }, { passive: true });

  window.addEventListener("resize", () => {
    const overlay = document.getElementById("bs-meter-global-overlay");
    const badge = document.querySelector(".bs-meter-profile-badge");
    if (overlay && overlay.classList.contains("visible") && badge) {
      positionOverlay(overlay, badge);
    }
  });

  // Hook SPA history state transitions
  const _pushState = history.pushState;
  history.pushState = function (...args) {
    _pushState.apply(this, args);
    setTimeout(checkCurrentPage, 60);
  };

  const _replaceState = history.replaceState;
  history.replaceState = function (...args) {
    _replaceState.apply(this, args);
    setTimeout(checkCurrentPage, 60);
  };

  window.addEventListener("popstate", () => setTimeout(checkCurrentPage, 60));

  // Monitor DOM mutations
  const observer = new MutationObserver(() => {
    if (currentProfileHandle) {
      const headerContainer = findProfileHeaderElement();
      if (headerContainer && !headerContainer.querySelector(`.bs-meter-profile-badge[data-handle="${currentProfileHandle}"]`)) {
        renderProfileBadge(headerContainer, currentProfileHandle, currentProfileData || { status: "queued", badge_level: "pending" });
      }
    }
  });

  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  } else {
    document.addEventListener("DOMContentLoaded", () => {
      observer.observe(document.body, { childList: true, subtree: true });
    });
  }

  checkCurrentPage();
  setInterval(checkCurrentPage, 1000);
})();
