// BS Meter Chrome Extension - True Floating Overlay for x.com / twitter.com

(function () {
  "use strict";

  let API_BASE_URL = null;
  let enabled = true;
  let isConfigLoaded = false;
  let currentProfileHandle = null;
  let currentProfileData = null;
  let hideOverlayTimeout = null;
  let isOverlayPinned = false;
  let pollingInterval = null;
  let pollAttempts = 0;

  console.info(
    `%c[BS Meter] Content script initialized on: ${window.location.href}`,
    "background: #1d4ed8; color: #ffffff; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 13px;"
  );

  // Reserved paths that are NOT user profiles
  const RESERVED_WORDS = new Set([
    "home", "explore", "notifications", "messages", "settings", "search",
    "i", "compose", "logout", "login", "tos", "privacy", "about", "trends",
    "hashtag", "lists", "bookmarks", "communities", "premium", "verified-orgs-signup"
  ]);

  const PROFILE_SUB_TABS = new Set([
    "with_replies", "highlights", "articles", "media", "likes", "superfollows"
  ]);

  // Load user settings asynchronously before any network calls are made
  function loadConfig() {
    return new Promise((resolve) => {
      if (typeof chrome === "undefined" || !chrome.storage) {
        isConfigLoaded = true;
        return resolve();
      }

      // Clean up legacy keys from earlier dev builds
      chrome.storage.local.remove(["envMode", "devApiUrl", "prodApiUrl"]).catch(() => {});
      if (chrome.storage.sync) {
        chrome.storage.sync.remove(["envMode", "devApiUrl", "prodApiUrl"]).catch(() => {});
      }

      chrome.storage.local.get(["apiBaseUrl", "enabled"], (local) => {
        if (local && local.apiBaseUrl) {
          API_BASE_URL = local.apiBaseUrl.replace(/\/+$/, "");
          if (typeof local.enabled === "boolean") enabled = local.enabled;
          isConfigLoaded = true;
          console.info(`%c[BS Meter] Active API Address: ${API_BASE_URL}`, "color: #38bdf8; font-weight: bold;");
          return resolve();
        }

        // Fallback to sync storage
        if (chrome.storage.sync) {
          chrome.storage.sync.get(["apiBaseUrl", "enabled"], (sync) => {
            if (sync && sync.apiBaseUrl) {
              API_BASE_URL = sync.apiBaseUrl.replace(/\/+$/, "");
              if (typeof sync.enabled === "boolean") enabled = sync.enabled;
            }
            isConfigLoaded = true;
            console.info(`%c[BS Meter] Active API Address: ${API_BASE_URL || "(none configured)"}`, "color: #38bdf8; font-weight: bold;");
            resolve();
          });
        } else {
          isConfigLoaded = true;
          resolve();
        }
      });
    });
  }

  // Listen for real-time config updates pushed from popup.js
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === "CONFIG_UPDATED") {
        if (msg.apiBaseUrl !== undefined) API_BASE_URL = (msg.apiBaseUrl || "").replace(/\/+$/, "");
        if (typeof msg.enabled === "boolean") enabled = msg.enabled;
        console.info(`%c[BS Meter] Real-time config applied: API = ${API_BASE_URL || "(none)"}`, "color: #10b981; font-weight: bold;");
        currentProfileHandle = null;
        removeExistingBadges();
        if (enabled && API_BASE_URL) {
          checkCurrentPage();
        }
      }
    });
  }

  // Listen for storage changes
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener((changes) => {
      let updated = false;
      if (changes.apiBaseUrl) {
        API_BASE_URL = (changes.apiBaseUrl.newValue || "").replace(/\/+$/, "");
        updated = true;
      }
      if (changes.enabled) {
        enabled = changes.enabled.newValue;
        updated = true;
      }
      if (updated) {
        console.info(`%c[BS Meter] Storage updated: API = ${API_BASE_URL}`, "color: #10b981; font-weight: bold;");
        currentProfileHandle = null;
        removeExistingBadges();
        if (enabled && API_BASE_URL) {
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

  // All report fields originate outside the extension (posts, model output,
  // and web-search results). Never interpolate them into HTML unescaped.
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    })[char]);
  }

  function safeHttpUrl(value) {
    try {
      const url = new URL(value);
      return (url.protocol === "https:" || url.protocol === "http:") ? url.href : null;
    } catch {
      return null;
    }
  }

  function safePercent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : 0;
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
      const falsehood = safePercent(dims.falsehood);
      const spam = safePercent(dims.spam);
      const harm = safePercent(dims.harm);

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
                  • <b>${escapeHtml((f.type || "SIGNAL").toUpperCase())}:</b> ${escapeHtml(f.summary)}
                  ${safeHttpUrl(f.evidence_url) ? `<a href="${escapeHtml(safeHttpUrl(f.evidence_url))}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link">Source: ${escapeHtml(f.evidence_title || "Context.dev Evidence")}</a>` : ""}
                </div>`
              )
              .join("")}
          </div>
        `;
      }

      overlay.innerHTML = `
        <div class="bs-popover-header">
          <span class="bs-popover-title">@${escapeHtml(handle)} - Evidence Report</span>
          <div style="display:flex; align-items:center;">
            <span class="bs-popover-score" style="color: ${level === "low" ? "#10b981" : level === "medium" ? "#f59e0b" : "#f87171"}">
              BS: ${score}/100
            </span>
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
          <span class="bs-popover-title">@${escapeHtml(handle)}</span>
          <div style="display:flex; align-items:center;">
            <span class="bs-popover-score" style="color:#9ca3af;">Analyzing...</span>
          </div>
        </div>
        <div style="font-size:11px; color:#9ca3af; line-height:1.45;">
          Context.dev is scraping recent tweets, uncloaking shortened links, and verifying claims against authoritative web evidence. Results refresh automatically.
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
      'div[data-testid="primaryColumn"] [data-testid="UserName"]',
      'div[data-testid="primaryColumn"] h2',
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
    pollAttempts = 0;
  }

  // Request BS Index evaluation for handle via Extension Background Service Worker
  async function fetchProfileBSIndex(handle) {
    if (!API_BASE_URL) {
      console.warn("[BS Meter] API Address not configured yet. Please open the BS Meter popup and enter your API URL.");
      return null;
    }

    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
      try {
        const response = await new Promise((resolve) => {
          const timeout = setTimeout(() => {
            console.warn("[BS Meter] Background message timed out after 10s");
            resolve(null);
          }, 10000);

          chrome.runtime.sendMessage(
            { type: "BATCH_LOOKUP", usernames: [handle], apiUrl: API_BASE_URL },
            (res) => {
              clearTimeout(timeout);
              if (chrome.runtime.lastError) {
                console.warn("[BS Meter] Background worker error:", chrome.runtime.lastError.message);
                resolve(null);
              } else {
                resolve(res);
              }
            }
          );
        });

        if (response && response.success && response.data && response.data.results) {
          const result = response.data.results[handle];
          if (result) {
            console.info(`[BS Meter] Evaluation for @${handle}: status=${result.status}, score=${result.bs_index}`);
            return result;
          }
        } else if (response && !response.success) {
          console.warn("[BS Meter] API error response:", response.error);
        }
      } catch (err) {
        if (err.message && err.message.includes("Extension context invalidated")) {
          console.warn("[BS Meter] Extension updated. Please refresh this page (Cmd+Shift+R) to reconnect.");
          return null;
        }
        console.warn("[BS Meter] Background request failed:", err);
      }
    } else {
      console.warn("[BS Meter] Extension context disconnected. Please refresh the page.");
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
    } else if (status === "analyzing") {
      labelText = "⏳ Scraping & Verifying Claims...";
    }

    badge.className = `bs-meter-profile-badge bs-badge-${level}`;
    badge.innerText = labelText;

    const overlay = document.getElementById("bs-meter-global-overlay");
    if (overlay && overlay.classList.contains("visible")) {
      showOverlay(badge, handle, data);
    }
  }

  function startPolling(handle) {
    if (pollingInterval) clearInterval(pollingInterval);
    pollAttempts = 0;

    pollingInterval = setInterval(async () => {
      pollAttempts++;
      if (currentProfileHandle !== handle) {
        clearInterval(pollingInterval);
        return;
      }

      if (pollAttempts > 35) { // 35 * 3s = 105s timeout
        console.warn(`[BS Meter] Polling timeout reached for @${handle}`);
        clearInterval(pollingInterval);
        return;
      }

      const updated = await fetchProfileBSIndex(handle);
      if (updated && currentProfileHandle === handle) {
        currentProfileData = updated;
        const header = findProfileHeaderElement();
        if (header) renderProfileBadge(header, handle, updated);

        if (updated.status === "ready") {
          console.info(`[BS Meter] Analysis COMPLETE for @${handle}! BS Index: ${updated.bs_index}`);
          clearInterval(pollingInterval);
        }
      }
    }, 3000);
  }

  function checkCurrentPage() {
    if (!enabled || !isConfigLoaded) return;
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
      console.info(
        `%c[BS Meter] Profile detected: @${handle}`,
        "color: #10b981; font-weight: bold; font-size: 14px;"
      );
      currentProfileHandle = handle;
      currentProfileData = { status: "queued", badge_level: "pending" };
      removeExistingBadges();

      // Mount pending badge immediately
      const initialContainer = findProfileHeaderElement();
      if (initialContainer) {
        renderProfileBadge(initialContainer, handle, currentProfileData);
      }

      // Trigger fetch and begin polling
      fetchProfileBSIndex(handle).then((data) => {
        if (currentProfileHandle === handle) {
          if (data) {
            currentProfileData = data;
            const container = findProfileHeaderElement();
            if (container) renderProfileBadge(container, handle, data);

            if (data.status === "ready") {
              console.info(`[BS Meter] Instant cached evaluation ready for @${handle}`);
              return;
            }
          }
          // Start polling for async pipeline completion
          startPolling(handle);
        }
      });
    }

    // Reattach badge if header re-rendered by React / Virtual DOM
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

  // Monitor DOM mutations (React view updates)
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

  // Wait for stored settings to load before starting
  loadConfig().then(() => {
    checkCurrentPage();
    setInterval(checkCurrentPage, 1000);
  });
})();
