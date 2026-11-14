// BS Meter Chrome Extension - True Floating Overlay for x.com / twitter.com

(function () {
  "use strict";

  // Lucide SVG Icons Dictionary
  const LUCIDE_ICONS = {
    "bar-chart-2":
      '<line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line>',
    "message-square":
      '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>',
    scale:
      '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"></path><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"></path><path d="M7 21h10"></path><path d="M12 3v18"></path><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"></path>',
    compass:
      '<circle cx="12" cy="12" r="10"></circle><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"></path><line x1="4" y1="22" x2="4" y2="15"></line>',
    search:
      '<circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>',
    "external-link":
      '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line>',
    x: '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>',
    "check-circle-2":
      '<path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"></path><path d="m9 12 2 2 4-4"></path>',
    "alert-triangle":
      '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line>',
    "alert-octagon":
      '<polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"></polygon><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line>',
    "loader-2": '<path d="M21 12a9 9 0 1 1-6.219-8.56"></path>',
  };

  function lucideIcon(name, size = 14, extraClass = "") {
    const inner = LUCIDE_ICONS[name] || "";
    const cls = `bs-icon${extraClass ? ` ${extraClass}` : ""}`;
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${cls}">${inner}</svg>`;
  }

  // Detect whether Twitter / X or system OS is in dark mode
  function isDarkMode() {
    try {
      const docStyle = document.documentElement.getAttribute("style") || "";
      if (docStyle.includes("color-scheme: light")) return false;
      if (docStyle.includes("color-scheme: dark")) return true;

      if (document.body) {
        const bg = window.getComputedStyle(document.body).backgroundColor;
        const match = bg.match(
          /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/,
        );
        if (match) {
          const alpha = match[4] !== undefined ? parseFloat(match[4]) : 1;
          if (alpha > 0.1) {
            const r = parseInt(match[1], 10);
            const g = parseInt(match[2], 10);
            const b = parseInt(match[3], 10);
            const lum = 0.299 * r + 0.587 * g + 0.114 * b;
            return lum < 128;
          }
        }
      }

      if (
        window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: light)").matches
      ) {
        return false;
      }
    } catch {
      // fallback to dark
    }
    return true;
  }

  function getActiveTheme() {
    return isDarkMode() ? "dark" : "light";
  }

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
    "background: #1d4ed8; color: #ffffff; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 13px;",
  );

  // Reserved paths that are NOT user profiles
  const RESERVED_WORDS = new Set([
    "home",
    "explore",
    "notifications",
    "messages",
    "settings",
    "search",
    "i",
    "compose",
    "logout",
    "login",
    "tos",
    "privacy",
    "about",
    "trends",
    "hashtag",
    "lists",
    "bookmarks",
    "communities",
    "premium",
    "verified-orgs-signup",
  ]);

  const PROFILE_SUB_TABS = new Set([
    "with_replies",
    "highlights",
    "articles",
    "media",
    "likes",
    "superfollows",
  ]);

  // Load user settings asynchronously before any network calls are made
  function loadConfig() {
    return new Promise((resolve) => {
      if (typeof chrome === "undefined" || !chrome.storage) {
        isConfigLoaded = true;
        return resolve();
      }

      // Clean up legacy keys from earlier dev builds
      chrome.storage.local
        .remove(["envMode", "devApiUrl", "prodApiUrl"])
        .catch(() => {});
      if (chrome.storage.sync) {
        chrome.storage.sync
          .remove(["envMode", "devApiUrl", "prodApiUrl"])
          .catch(() => {});
      }

      chrome.storage.local.get(["apiBaseUrl", "enabled"], (local) => {
        if (local && local.apiBaseUrl) {
          API_BASE_URL = local.apiBaseUrl.replace(/\/+$/, "");
          if (typeof local.enabled === "boolean") enabled = local.enabled;
          isConfigLoaded = true;
          console.info(
            `%c[BS Meter] Active API Address: ${API_BASE_URL}`,
            "color: #38bdf8; font-weight: bold;",
          );
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
            console.info(
              `%c[BS Meter] Active API Address: ${API_BASE_URL || "(none configured)"}`,
              "color: #38bdf8; font-weight: bold;",
            );
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
  if (
    typeof chrome !== "undefined" &&
    chrome.runtime &&
    chrome.runtime.onMessage
  ) {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === "CONFIG_UPDATED") {
        if (msg.apiBaseUrl !== undefined)
          API_BASE_URL = (msg.apiBaseUrl || "").replace(/\/+$/, "");
        if (typeof msg.enabled === "boolean") enabled = msg.enabled;
        console.info(
          `%c[BS Meter] Real-time config applied: API = ${API_BASE_URL || "(none)"}`,
          "color: #10b981; font-weight: bold;",
        );
        currentProfileHandle = null;
        removeExistingBadges();
        if (enabled && API_BASE_URL) {
          checkCurrentPage();
        }
      }
    });
  }

  // Listen for storage changes
  if (
    typeof chrome !== "undefined" &&
    chrome.storage &&
    chrome.storage.onChanged
  ) {
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
        console.info(
          `%c[BS Meter] Storage updated: API = ${API_BASE_URL}`,
          "color: #10b981; font-weight: bold;",
        );
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
        document.addEventListener("DOMContentLoaded", () =>
          document.body.appendChild(overlay),
        );
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
    if (
      top + overlayHeight > window.innerHeight - padding &&
      rect.top > overlayHeight + padding
    ) {
      top = rect.top - overlayHeight - 8;
    }

    overlay.style.top = `${Math.round(top)}px`;
    overlay.style.left = `${Math.round(left)}px`;
  }

  // All report fields originate outside the extension (posts, model output,
  // and web-search results). Never interpolate them into HTML unescaped.
  function escapeHtml(value) {
    return String(value ?? "").replace(
      /[&<>'"]/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          "'": "&#39;",
          '"': "&quot;",
        })[char],
    );
  }

  function safeHttpUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:"
        ? url.href
        : null;
    } catch {
      return null;
    }
  }

  const MEDIA_EXTENSIONS = [
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".svg",
    ".bmp",
    ".ico",
    ".mp4",
    ".mov",
    ".avi",
    ".webm",
    ".mkv",
    ".m4v",
    ".flv",
    ".wmv",
  ];
  const MEDIA_HOSTS = [
    "pbs.twimg.com",
    "video.twimg.com",
    "twimg.com",
    "ton.twitter.com",
  ];

  function isExternalLink(value, username = "") {
    if (!value || typeof value !== "string") return false;
    const clean = value.trim();
    try {
      const url = new URL(
        clean.startsWith("http") ? clean : "https://" + clean,
      );
      if (url.protocol !== "http:" && url.protocol !== "https:") return false;
      const domain = (url.hostname || "").toLowerCase().replace(/^www\./, "");
      if (
        !domain ||
        domain === "x.com" ||
        domain === "twitter.com" ||
        domain === "mobile.twitter.com" ||
        MEDIA_HOSTS.includes(domain)
      ) {
        return false;
      }
      const path = (url.pathname || "").toLowerCase();
      if (path.includes("share") || path.includes("intent")) {
        return false;
      }
      const cleanPath = path.split("?")[0].split("#")[0];
      if (MEDIA_EXTENSIONS.some((ext) => cleanPath.endsWith(ext))) {
        return false;
      }
      const cleanUser = (username || "").toLowerCase().replace(/^@/, "");
      if (cleanUser && path.replace(/^\/+/, "").split("/")[0] === cleanUser) {
        return false;
      }
      return domain.includes(".");
    } catch {
      return false;
    }
  }

  function resolveApiUrl(pathOrUrl) {
    if (!pathOrUrl) return null;
    if (typeof pathOrUrl !== "string") return null;
    if (pathOrUrl.startsWith("http://") || pathOrUrl.startsWith("https://")) {
      return safeHttpUrl(pathOrUrl);
    }
    const base = (API_BASE_URL || "").replace(/\/+$/, "");
    if (base && pathOrUrl.startsWith("/")) {
      return safeHttpUrl(`${base}${pathOrUrl}`);
    }
    return null;
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

    // Do not show layover box when the profile is being verified or pending
    if (!data || data.status !== "ready") {
      hideOverlay(true);
      return;
    }

    const overlay = getOrCreateGlobalOverlay();
    overlay.setAttribute("data-bs-theme", getActiveTheme());
    const level = data.badge_level ? data.badge_level : "pending";
    const score =
      typeof data.bs_index === "number" ? Math.round(data.bs_index) : null;

    const dims = data.dimensions || {};
    const falsehood = safePercent(dims.falsehood);
    const spam = safePercent(dims.spam);
    const harm = safePercent(dims.harm);
    const propaganda = safePercent(dims.propaganda);

    let flagsHtml = "";
    if (data.top_flags && data.top_flags.length > 0) {
      flagsHtml = `
        <div class="bs-flags-section">
          <div class="bs-flag-header">Flagged Signals:</div>
          ${data.top_flags
            .slice(0, 3)
            .map(
              (f) => `
              <div class="bs-flag-item">
                • <b>${escapeHtml((f.type || "SIGNAL").toUpperCase())}:</b> ${escapeHtml(f.summary)}
                ${safeHttpUrl(f.evidence_url) ? `<a href="${escapeHtml(safeHttpUrl(f.evidence_url))}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link">Source: ${escapeHtml(f.evidence_title || "Context.dev Evidence")}</a>` : ""}
              </div>`,
            )
            .join("")}
        </div>
      `;
    }

    overlay.innerHTML = `
      <div class="bs-popover-header">
          <span class="bs-popover-score" style="color: ${level === "low" ? "#10b981" : level === "medium" ? "#f59e0b" : "#f87171"}">
            BS: ${score}/100
          </span>
  
            <button id="bsInspectBtn" class="bs-inspect-btn" type="button">
              ${lucideIcon("search", 13)} BS Deep Dive
            </button>
   
        
      </div>
      <div>
        <div class="bs-metric-row"><span>Falsehoods / Claims:</span><span>${Math.round(falsehood)}%</span></div>
        <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${falsehood}%; background:#38bdf8;"></div></div>

        <div class="bs-metric-row"><span>Spam & Scams:</span><span>${Math.round(spam)}%</span></div>
        <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${spam}%; background:#f59e0b;"></div></div>

        <div class="bs-metric-row"><span>Harm & Toxicity:</span><span>${Math.round(harm)}%</span></div>
        <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${harm}%; background:#f87171;"></div></div>

        <div class="bs-metric-row"><span>Propaganda:</span><span>${Math.round(propaganda)}%</span></div>
        <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${propaganda}%; background:#a855f7;"></div></div>
      </div>
      ${flagsHtml}
      
    `;

    const inspectBtn = overlay.querySelector("#bsInspectBtn");
    if (inspectBtn) {
      inspectBtn.onclick = (e) => {
        e.stopPropagation();
        e.preventDefault();
        hideOverlay(true);
        openInspectModal(handle, data);
      };
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

  let activeModalKeydownHandler = null;

  function closeInspectModal() {
    const backdrop = document.getElementById("bs-inspect-modal-backdrop");
    if (backdrop) {
      backdrop.classList.remove("visible");
    }
    if (activeModalKeydownHandler) {
      document.removeEventListener("keydown", activeModalKeydownHandler);
      activeModalKeydownHandler = null;
    }
    document.body.style.removeProperty("overflow");
  }

  function getOrCreateInspectModalBackdrop() {
    let backdrop = document.getElementById("bs-inspect-modal-backdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "bs-inspect-modal-backdrop";
      backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) {
          closeInspectModal();
        }
      });
      document.body.appendChild(backdrop);
    }
    return backdrop;
  }

  function renderInspectModalContent(backdrop, handle, data) {
    const details = (data && data.details) || {};
    const dims = (data && data.dimensions) || {};
    const level = (data && data.badge_level) || "pending";
    const score =
      data && typeof data.bs_index === "number"
        ? Math.round(data.bs_index)
        : "—";

    let summary = (data && data.summary) || details.summary;
    if (
      !summary ||
      typeof summary !== "string" ||
      summary.trim().length === 0
    ) {
      if (level === "low") {
        summary = `Context.dev analysis evaluated @${escapeHtml(handle)} and found low credibility risk (${score}/100 BS Index). Public content exhibits consistency with minimal deceptive, spam, or manipulative patterns.`;
      } else if (level === "medium") {
        summary = `Context.dev analysis evaluated @${escapeHtml(handle)} and noted moderate credibility concerns (${score}/100 BS Index). Several promotional statements, ungrounded claims, or uncloaked links warrant caution.`;
      } else {
        summary = `Context.dev analysis evaluated @${escapeHtml(handle)} and identified elevated risk factors (${score}/100 BS Index). Flagged signals include unverified assertions, sensational claims, or potential spam/phishing links.`;
      }
    }

    const postsAnalyzed =
      details.posts_analyzed || (data && data.posts_analyzed) || 10;
    const postsList = details.posts || (data && data.posts) || [];
    const scrapedLinks =
      details.scraped_links || (data && data.scraped_links) || [];
    const topFlags = (data && data.top_flags) || [];

    // Filter to strictly external links from posts (bio, images, and videos excluded)
    const cleanScrapedLinks = scrapedLinks.filter((l) => {
      const domain = (l.domain || "").toLowerCase().replace(/^www\./, "");
      if (
        domain === "x.com" ||
        domain === "twitter.com" ||
        domain === "mobile.twitter.com"
      )
        return false;
      if (!isExternalLink(l.url, handle)) return false;
      const title = (l.title || "").toLowerCase();
      if (
        title.includes("share profile") ||
        title.includes("share on x") ||
        title.includes("on x / x")
      )
        return false;
      return true;
    });
    const totalExternalLinks = cleanScrapedLinks.length;

    let claims = details.claims || [];
    if ((!claims || claims.length === 0) && topFlags.length > 0) {
      claims = topFlags.map((f) => ({
        claim: f.summary,
        assessment:
          f.assessment ||
          (f.type === "falsehood" ? "contradicted" : "uncertain"),
        reason: `Signal flagged during grounding verification (${f.type || "signal"}).`,
        evidence: f.evidence_url
          ? [
              {
                url: f.evidence_url,
                title: f.evidence_title || "Grounding Source",
                source_domain: "Evidence Reference",
                excerpt: f.summary,
              },
            ]
          : [],
      }));
    }
    const claimsVerified = details.claims_verified || claims.length;
    const propaganda = details.propaganda_profile || [];
    const propagandaAvailable =
      details.propaganda_analysis_status === "analyzed";
    const propagandaScore =
      typeof dims.propaganda === "number"
        ? dims.propaganda
        : Math.round(
            Math.max(
              0,
              ...(propaganda || []).map((p) => Number(p.score) || 0),
            ) * 100,
          );
    const activeTechniquesCount = propaganda.filter(
      (item) => (Number(item.score) || 0) > 0 || (Number(item.count) || 0) > 0,
    ).length;
    const evidenceBreakdown = details.claim_evidence_breakdown || {};
    const scrapedLinksCount =
      details.scraped_links_count || cleanScrapedLinks.length;
    const totalLinks = cleanScrapedLinks.length;
    const rawReportUrl = resolveApiUrl(data && data.report_url);

    const levelColor =
      level === "low"
        ? "#10b981"
        : level === "medium"
          ? "#f59e0b"
          : level === "high"
            ? "#f87171"
            : "#9ca3af";
    const levelText =
      level === "low"
        ? "Low Risk / Clean"
        : level === "medium"
          ? "Moderate Risk"
          : level === "high"
            ? "High Risk"
            : "Pending";

    const propagandaHtml = propaganda.length
      ? propaganda
          .map((item) => {
            const count = Math.max(0, Number(item.count) || 0);
            const techniqueScore = Math.max(
              0,
              Math.min(1, Number(item.score) || 0),
            );
            const examples = (item.examples || [])
              .slice(0, 2)
              .map(
                (example) => `
        <div class="bs-signal-example">“${escapeHtml(example.matched_text || example.text || "")}”${example.post_id ? ` <span>· post ${escapeHtml(example.post_id)}</span>` : ""}${example.reason ? `<div class="bs-profile-signal-description">${escapeHtml(example.reason)}</div>` : ""}</div>
      `,
              )
              .join("");
            return `<div class="bs-profile-signal">
        <div class="bs-profile-signal-heading"><b>${escapeHtml(item.name || item.key || "Technique")}</b><span>${techniqueScore.toFixed(2)} score · ${count} ${count === 1 ? "post" : "posts"}</span></div>
        <div class="bs-profile-signal-track"><div style="width:${techniqueScore * 100}%"></div></div>
        <div class="bs-profile-signal-description">${escapeHtml(item.description || "")}</div>
        ${examples}
      </div>`;
          })
          .join("")
      : `<div class="bs-inspect-card-content">Technique analysis is not available for this report.</div>`;

    const evidenceCategories = evidenceBreakdown.categories || [];
    function getEvidenceCount(matchKey) {
      const found = evidenceCategories.find((c) =>
        (c.label || "").toLowerCase().includes(matchKey),
      );
      if (found && typeof found.count === "number") return found.count;
      return claims.filter((c) => {
        const a = (c.assessment || "").toLowerCase();
        if (matchKey === "support")
          return a.includes("support") && !a.includes("partial");
        if (matchKey === "contradict")
          return (
            a.includes("contradict") ||
            a.includes("false") ||
            a.includes("refut")
          );
        if (matchKey === "missing")
          return a.includes("partial") || a.includes("missing");
        if (matchKey === "unverified")
          return a.includes("uncertain") || a.includes("unverified");
        return false;
      }).length;
    }

    const countSupported = getEvidenceCount("support");
    const countContradicted = getEvidenceCount("contradict");
    const countMissingContext = getEvidenceCount("missing");
    const countUnverified = getEvidenceCount("unverified");

    // Claims HTML
    let claimsHtml = "";
    if (claims.length > 0) {
      claimsHtml = claims
        .map((c) => {
          const assessment = (c.assessment || "uncertain").toLowerCase();
          let stanceClass = "bs-stance-uncertain";
          if (
            assessment.includes("support") &&
            !assessment.includes("partial")
          ) {
            stanceClass = "bs-stance-supported";
          } else if (
            assessment.includes("contradict") ||
            assessment.includes("refut") ||
            assessment.includes("false")
          ) {
            stanceClass = "bs-stance-contradicted";
          } else if (
            assessment.includes("partial") ||
            assessment.includes("missing")
          ) {
            stanceClass = "bs-stance-partially_supported";
          }

          const sources = c.evidence || [];
          const sourcesHtml = sources
            .slice(0, 3)
            .map(
              (s) => `
          <div class="bs-evidence-source-item">
            • <b>${escapeHtml(s.source_domain || "Source")}:</b> 
            ${safeHttpUrl(s.url) ? `<a href="${escapeHtml(safeHttpUrl(s.url))}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link" style="display:inline;">${escapeHtml(s.title || s.url)}</a>` : escapeHtml(s.title || s.url || "")}
            ${s.excerpt ? `<div class="bs-source-quote">“${escapeHtml(s.excerpt.slice(0, 160))}…”</div>` : ""}
          </div>
        `,
            )
            .join("");

          return `
          <div class="bs-claim-card">
            <div class="bs-claim-header">
              <span class="bs-claim-text">“${escapeHtml(c.claim || "")}”</span>
              <span class="bs-stance-pill ${stanceClass}">${escapeHtml((c.assessment || "uncertain").replace(/_/g, " "))}</span>
            </div>
            ${c.reason ? `<div class="bs-claim-reason">${escapeHtml(c.reason)}</div>` : ""}
            ${sources.length > 0 ? `<div class="bs-claim-evidence-sources">${sourcesHtml}</div>` : ""}
          </div>
        `;
        })
        .join("");
    } else {
      claimsHtml = `<div class="bs-inspect-card-content" style="color:var(--bs-text-secondary); font-style:italic;">No disputed factual claims were asserted in the sampled posts.</div>`;
    }

    // Flags HTML
    let flagsDetailHtml = "";
    if (topFlags.length > 0) {
      flagsDetailHtml = topFlags
        .map(
          (f) => `
        <div class="bs-flag-item">
          • <b>${escapeHtml((f.type || "SIGNAL").toUpperCase())}:</b> ${escapeHtml(f.summary)}
          ${safeHttpUrl(f.evidence_url) ? `<a href="${escapeHtml(safeHttpUrl(f.evidence_url))}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link">Evidence Source: ${escapeHtml(f.evidence_title || "Context.dev Source")}</a>` : ""}
        </div>
      `,
        )
        .join("");
    } else {
      flagsDetailHtml = `<div style="color:var(--bs-status-supported-text); font-size:11px; display:flex; align-items:center; gap:6px;">${lucideIcon("check-circle-2", 13)} No major credibility flags or scam signals detected.</div>`;
    }

    // Section 1: Overview HTML (without stat grid boxes)
    const overviewHtml = `
      <!-- Executive Summary -->
      <div class="bs-summary-box">
        <div class="bs-summary-label">Summary</div>
        <p class="bs-summary-text">${escapeHtml(summary)}</p>
      </div>

      <!-- Signal Dimensions & Flags -->
      <div class="bs-inspect-section">
        <div class="bs-section-title">${lucideIcon("flag", 14)} Dimensions of BS</div>
        <div class="bs-inspect-card" style="margin-bottom:10px;">
          <div class="bs-metric-row"><span>Falsehood & Deception:</span><span>${Math.round(safePercent(dims.falsehood))}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${safePercent(dims.falsehood)}%; background:#38bdf8;"></div></div>

          <div class="bs-metric-row"><span>Spam, Phishing & Scams:</span><span>${Math.round(safePercent(dims.spam))}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${safePercent(dims.spam)}%; background:#f59e0b;"></div></div>

          <div class="bs-metric-row"><span>Toxicity & Harm:</span><span>${Math.round(safePercent(dims.harm))}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${safePercent(dims.harm)}%; background:#f87171;"></div></div>

          <div class="bs-metric-row"><span>Propaganda & Rhetoric:</span><span>${Math.round(safePercent(propagandaScore))}%</span></div>
          <div class="bs-bar-container"><div class="bs-bar-fill" style="width:${safePercent(propagandaScore)}%; background:#a855f7;"></div></div>
        </div>
        <div class="bs-inspect-card">
          <div style="font-weight:700; color:var(--bs-text-primary); margin-bottom:6px; font-size:11px;">Active Flags:</div>
          ${flagsDetailHtml}
        </div>
      </div>
    `;

    // Section 2: Posts Checked HTML
    let postsHtml = "";
    if (postsList.length > 0) {
      postsHtml = `
        <div class="bs-inspect-section">
          <div class="bs-section-title">${lucideIcon("message-square", 14)} Posts Checked (${postsList.length})</div>
          ${postsList
            .map((p, idx) => {
              const postNum = idx + 1;
              const postId = p.id || `${postNum}`;
              const postUrl =
                p.url || `https://x.com/${handle}/status/${postId}`;
              const postLinks = p.links || [];
              const matchedClaims = claims.filter(
                (c) =>
                  c.post_id === postId ||
                  (c.claim &&
                    p.text &&
                    p.text
                      .toLowerCase()
                      .includes(c.claim.toLowerCase().slice(0, 30))),
              );

              return `
              <div class="bs-post-card">
                <div class="bs-post-header">
                  <span class="bs-post-id-badge">Post #${postNum} · ID: ${escapeHtml(postId)}</span>
                  ${safeHttpUrl(postUrl) ? `<a href="${escapeHtml(safeHttpUrl(postUrl))}" target="_blank" rel="noopener noreferrer" class="bs-post-x-link">View on x.com ${lucideIcon("external-link", 11)}</a>` : ""}
                </div>
                <div class="bs-post-text">“${escapeHtml(p.text || "")}”</div>
                ${
                  postLinks.length > 0
                    ? `
                  <div class="bs-post-links">
                    <span style="color:var(--bs-text-secondary); font-weight:600;">Embedded Links:</span>
                    ${postLinks.map((l) => (safeHttpUrl(l) ? `<a href="${escapeHtml(safeHttpUrl(l))}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link">${escapeHtml(l)}</a>` : escapeHtml(l))).join(" ")}
                  </div>
                `
                    : ""
                }
                ${
                  matchedClaims.length > 0
                    ? `
                  <div class="bs-post-matched-claims">
                    <span style="font-weight:700;">Flagged Assertion:</span>
                    ${matchedClaims.map((c) => `“${escapeHtml(c.claim || "")}” [${escapeHtml((c.assessment || "evaluated").replace(/_/g, " "))}]`).join("; ")}
                  </div>
                `
                    : ""
                }
              </div>
            `;
            })
            .join("")}
        </div>
      `;
    } else {
      postsHtml = `
        <div class="bs-inspect-section">
          <div class="bs-section-title">${lucideIcon("message-square", 14)}Posts Analysed (${postsAnalyzed})</div>
          <div class="bs-inspect-card">
            <div class="bs-inspect-card-content">
              <div style="font-weight:700; color:var(--bs-text-primary); margin-bottom:6px;">Sampled Posts Summary</div>
              <div>Context.dev sampled <b>${postsAnalyzed} recent posts</b> from @${escapeHtml(handle)} for factual claim extraction, rhetoric patterns, and destination link uncloaking.</div>
            </div>
          </div>
        </div>
      `;
    }

    // Section 3: Links Checked HTML (Strictly external links from sampled posts; bio and media excluded)
    let uncloakedSectionHtml = "";
    if (cleanScrapedLinks.length > 0) {
      uncloakedSectionHtml = `
        <div class="bs-inspect-section">
          <div class="bs-section-title">${lucideIcon("link", 14)} Post and Profile Links Analysed (${totalExternalLinks})</div>
          ${cleanScrapedLinks
            .map((l) => {
              const url = l.url || "";
              const domain =
                l.domain || (url ? url.split("/")[2] || "" : "External");
              const source = l.source
                ? l.source.startsWith("tweet-")
                  ? `Post ${l.source.replace("tweet-", "")}`
                  : l.source
                : "Sampled Post";
              const title = l.title || "External Destination Page";
              const preview =
                l.preview || (l.markdown ? l.markdown.slice(0, 250) : "");
              const isError = Boolean(l.error);

              return `
              <div class="bs-link-card">
                <div class="bs-link-header">
                  <div style="display:flex; align-items:center; gap:8px; flex:1; overflow:hidden;">
                    <span class="bs-link-domain-badge">${escapeHtml(domain)}</span>
                    <span class="bs-link-source-badge">${escapeHtml(source)}</span>
                  </div>
                  ${safeHttpUrl(url) ? `<a href="${escapeHtml(safeHttpUrl(url))}" target="_blank" rel="noopener noreferrer" class="bs-evidence-link">Open URL ${lucideIcon("external-link", 11)}</a>` : ""}
                </div>
                <div class="bs-link-title">${escapeHtml(title)}</div>
                <div class="bs-link-url-text">${safeHttpUrl(url) ? escapeHtml(safeHttpUrl(url)) : escapeHtml(url)}</div>
                ${preview ? `<div class="bs-link-preview"><b>Page Preview:</b> “${escapeHtml(preview.trim())}…”</div>` : ""}
                ${isError ? `<div class="bs-link-error-note">${lucideIcon("alert-triangle", 12)} Note: Target URL could not be fully resolved or returned an error (${escapeHtml(l.error)})</div>` : ""}
              </div>
            `;
            })
            .join("")}
        </div>
      `;
    } else {
      uncloakedSectionHtml = `
        <div class="bs-inspect-section">
          <div class="bs-section-title">${lucideIcon("link", 14)} Links Checked</div>
          <div class="bs-inspect-card">
            <div class="bs-inspect-card-content" style="color:#10b981; display:flex; align-items:center; gap:6px;">
              ${lucideIcon("check-circle-2", 13)} No external links, shortened URLs, or commercial landing pages were detected in the sampled posts.
            </div>
          </div>
        </div>
      `;
    }

    const linksHtml = `
      <div class="bs-inspect-section">
        
        ${uncloakedSectionHtml}
      </div>
    `;

    // Section 4: Claims Verified HTML (Evidence Quality condensed into big colored numbers on top)
    const claimsSectionHtml = `
      <div class="bs-inspect-section" style="margin-bottom: 20px;">
        <div class="bs-section-title">${lucideIcon("search", 14)} Breakdown (${Number(evidenceBreakdown.total) || claims.length} stances)</div>
        <div class="bs-stat-grid" style="margin-bottom: 0;">
          <div class="bs-breakdown-chip bs-breakdown-chip-supported">
            <div class="bs-breakdown-val">${countSupported}</div>
            <div class="bs-breakdown-lbl">Supported</div>
          </div>
          <div class="bs-breakdown-chip bs-breakdown-chip-contradicted">
            <div class="bs-breakdown-val">${countContradicted}</div>
            <div class="bs-breakdown-lbl">Contradicted</div>
          </div>
          <div class="bs-breakdown-chip bs-breakdown-chip-missing">
            <div class="bs-breakdown-val">${countMissingContext}</div>
            <div class="bs-breakdown-lbl">Missing Context</div>
          </div>
          <div class="bs-breakdown-chip bs-breakdown-chip-unverified">
            <div class="bs-breakdown-val">${countUnverified}</div>
            <div class="bs-breakdown-lbl">Unverified</div>
          </div>
        </div>
      </div>

      <div class="bs-inspect-section">
        <div class="bs-section-title">${lucideIcon("scale", 14)} Verified Stances (${claims.length})</div>
        ${claimsHtml}
      </div>
    `;

    // Section 5: Propaganda Breakdown HTML
    const propagandaSectionHtml = `
      <div class="bs-inspect-section">
        <div class="bs-section-title">${lucideIcon("compass", 14)} Propaganda Indicators (${activeTechniquesCount} found in ${propaganda.length || 7})</div>
        <div class="bs-inspect-card">
          <div class="bs-profile-signals">${propagandaHtml}</div>
        </div>
      </div>
    `;

    const theme = getActiveTheme();
    backdrop.setAttribute("data-bs-theme", theme);

    backdrop.innerHTML = `
      <div class="bs-inspect-modal" role="dialog" aria-modal="true" data-bs-theme="${theme}">
        <!-- Header -->
        <div class="bs-modal-header">
          <div class="bs-modal-title-group">
            <h2 class="bs-modal-title">BS Deep Dive: @${escapeHtml(handle)}</h2>
            
          </div>
          <div class="bs-modal-title-group">
          <span class="bs-popover-score" style="color: ${level === "low" ? "#10b981" : level === "medium" ? "#f59e0b" : "#f87171"}">
            BS: ${score}/100
          </span>
          <button id="bsModalClose" class="bs-modal-close-btn" type="button" aria-label="Close">${lucideIcon("x", 16)}</button>
          </div>
        </div>

        <!-- Navigation Tabs with Highlighted Counts -->
        <div class="bs-modal-tabs" role="tablist">
          <button id="bsTabOverview" class="bs-tab-btn active" role="tab" aria-selected="true" type="button">${lucideIcon("bar-chart-2", 14)} Overview</button>
          <button id="bsTabPropaganda" class="bs-tab-btn" role="tab" aria-selected="false" type="button">
            ${lucideIcon("compass", 14)} Propaganda <span class="bs-tab-count">${activeTechniquesCount}</span>
          </button>
          <button id="bsTabStances" class="bs-tab-btn" role="tab" aria-selected="false" type="button">
            ${lucideIcon("scale", 14)} Stances <span class="bs-tab-count">${claims.length}</span>
          </button>
          <button id="bsTabPosts" class="bs-tab-btn" role="tab" aria-selected="false" type="button">
            ${lucideIcon("message-square", 14)} Posts <span class="bs-tab-count">${postsList.length || postsAnalyzed}</span>
          </button>
          <button id="bsTabLinks" class="bs-tab-btn" role="tab" aria-selected="false" type="button">
            ${lucideIcon("link", 14)} Links <span class="bs-tab-count">${totalExternalLinks}</span>
          </button>
        </div>

        <!-- Body with 5 Sections -->
        <div class="bs-modal-body">
          <div id="bsTabContentOverview" style="display:block;">
            ${overviewHtml}
          </div>
          <div id="bsTabContentPropaganda" style="display:none;">
            ${propagandaSectionHtml}
          </div>
          <div id="bsTabContentClaims" style="display:none;">
            ${claimsSectionHtml}
          </div>
          <div id="bsTabContentPosts" style="display:none;">
            ${postsHtml}
          </div>
          <div id="bsTabContentLinks" style="display:none;">
            ${linksHtml}
          </div>
        </div>
      </div>
    `;

    // Tab switcher logic
    const tabConfigs = [
      { btnId: "#bsTabOverview", contentId: "#bsTabContentOverview" },
      { btnId: "#bsTabPropaganda", contentId: "#bsTabContentPropaganda" },
      { btnId: "#bsTabStances", contentId: "#bsTabContentClaims" },
      { btnId: "#bsTabPosts", contentId: "#bsTabContentPosts" },
      { btnId: "#bsTabLinks", contentId: "#bsTabContentLinks" },
    ];

    function selectTab(targetIndex) {
      tabConfigs.forEach((cfg, idx) => {
        const btn = backdrop.querySelector(cfg.btnId);
        const content = backdrop.querySelector(cfg.contentId);
        if (!btn || !content) return;
        const isActive = idx === targetIndex;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
        content.style.display = isActive ? "block" : "none";
      });
    }

    tabConfigs.forEach((cfg, idx) => {
      const btn = backdrop.querySelector(cfg.btnId);
      if (btn) {
        btn.onclick = () => selectTab(idx);
      }
    });

    // Close button events
    const closeBtn = backdrop.querySelector("#bsModalClose");
    if (closeBtn) closeBtn.onclick = closeInspectModal;

    // Prevent closing when clicking inside modal
    const modalDialog = backdrop.querySelector(".bs-inspect-modal");
    if (modalDialog) {
      modalDialog.onclick = (e) => e.stopPropagation();
    }
  }

  function openInspectModal(handle, data) {
    const backdrop = getOrCreateInspectModalBackdrop();
    backdrop.setAttribute("data-bs-theme", getActiveTheme());
    renderInspectModalContent(backdrop, handle, data);

    // Keydown handler (Escape to close)
    if (activeModalKeydownHandler) {
      document.removeEventListener("keydown", activeModalKeydownHandler);
    }
    activeModalKeydownHandler = (e) => {
      if (e.key === "Escape") {
        closeInspectModal();
      }
    };
    document.addEventListener("keydown", activeModalKeydownHandler);

    // Prevent body background scroll while modal is open
    document.body.style.overflow = "hidden";
    backdrop.classList.add("visible");

    // Asynchronously fetch complete analysis if detailed claims are not loaded yet
    if (
      data &&
      data.report_url &&
      (!data.details ||
        !data.details.claims ||
        data.details.claims.length === 0)
    ) {
      if (
        typeof chrome !== "undefined" &&
        chrome.runtime &&
        chrome.runtime.id
      ) {
        chrome.runtime.sendMessage(
          {
            type: "GET_ANALYSIS",
            reportUrl: data.report_url,
            apiUrl: API_BASE_URL,
          },
          (res) => {
            if (res && res.success && res.data && res.data.result) {
              const fullResult = res.data.result;
              const enrichedData = {
                ...data,
                summary: fullResult.summary || data.summary,
                details: {
                  ...(data.details || {}),
                  ...fullResult,
                  summary:
                    fullResult.summary ||
                    (data.details && data.details.summary),
                  bio: fullResult.bio || (data.details && data.details.bio),
                  bio_links:
                    fullResult.bio_links ||
                    (data.details && data.details.bio_links),
                  posts_analyzed:
                    fullResult.posts_analyzed ||
                    (data.details && data.details.posts_analyzed),
                  posts:
                    fullResult.posts ||
                    (data.details && data.details.posts) ||
                    [],
                  scraped_links:
                    fullResult.scraped_links ||
                    (data.details && data.details.scraped_links) ||
                    [],
                  claims_verified:
                    fullResult.claims_verified ||
                    (data.details && data.details.claims_verified),
                  claims:
                    fullResult.claims || (data.details && data.details.claims),
                  scraped_links_count:
                    fullResult.scraped_links_count ||
                    (data.details && data.details.scraped_links_count),
                },
              };
              const currentBackdrop = document.getElementById(
                "bs-inspect-modal-backdrop",
              );
              if (
                currentBackdrop &&
                currentBackdrop.classList.contains("visible") &&
                currentProfileHandle === handle
              ) {
                renderInspectModalContent(
                  currentBackdrop,
                  handle,
                  enrichedData,
                );
              }
            }
          },
        );
      }
    }
  }

  function getProfileHandleFromUrl() {
    const cleanPath = window.location.pathname
      .split("?")[0]
      .split("#")[0]
      .replace(/^\/+|\/+$/g, "");
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
    } else if (
      segments.length === 2 &&
      PROFILE_SUB_TABS.has(segments[1].toLowerCase())
    ) {
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
      "main header",
    ];
    for (const selector of candidateSelectors) {
      const el = document.querySelector(selector);
      if (el) return el;
    }
    return null;
  }

  // Find the profile action buttons row (follow, chat, and three dots buttons)
  function findProfileActionsTarget() {
    const root =
      document.querySelector('div[data-testid="primaryColumn"]') || document;
    const tabList = root.querySelector(
      '[role="tablist"], [data-testid="ScrollSnap-List"]',
    );

    const buttonCandidates = [
      // Three dots / More options
      '[data-testid="userActions"]',
      'button[aria-label="More"]',
      'button[aria-label="More options"]',
      'div[role="button"][aria-label="More"]',
      'div[role="button"][aria-label="More options"]',
      // Chat / Direct Message
      '[data-testid="sendDMFromProfile"]',
      'a[href*="/messages/"][role="link"]',
      'div[role="button"][aria-label*="Message"]',
      'button[aria-label*="Message"]',
      // Follow / Unfollow
      '[data-testid$="-follow"]',
      '[data-testid$="-unfollow"]',
      '[data-testid="placementTracking"]',
      '[data-testid="userFollowIndicator"]',
      // Notification / Subscribe
      '[data-testid="userFollowNotificationButton"]',
      '[data-testid="subscribe"]',
      // Edit Profile (own account)
      '[data-testid="editProfileButton"]',
      '[data-testid="ProfileHeader_EditProfile_Button"]',
      'a[href="/settings/profile"]',
    ];

    const foundButtons = [];
    for (const sel of buttonCandidates) {
      const el = root.querySelector(sel);
      if (el) {
        if (tabList) {
          const isBeforeTabs =
            (el.compareDocumentPosition(tabList) &
              Node.DOCUMENT_POSITION_FOLLOWING) !==
            0;
          if (!isBeforeTabs) continue;
        }
        if (!foundButtons.includes(el)) {
          foundButtons.push(el);
        }
      }
    }

    if (foundButtons.length === 0) {
      return null;
    }

    // Identify the actions row container holding these action buttons
    let container = null;
    let referenceNode = null;

    // Check ancestors to find a shared flex container holding 2+ buttons
    for (const btn of foundButtons) {
      let p = btn.parentElement;
      while (p && p !== root && p !== document.body) {
        const matches = foundButtons.filter((b) => p.contains(b));
        if (matches.length >= 2) {
          container = p;
          break;
        }
        p = p.parentElement;
      }
      if (container) break;
    }

    // If only one button exists (e.g., own profile or only Follow button), find its direct button-group wrapper
    if (!container) {
      const primaryBtn = foundButtons[0];
      let p = primaryBtn.parentElement;
      while (p && p !== root && p !== document.body) {
        const style = window.getComputedStyle(p);
        if (
          style.display.includes("flex") &&
          style.flexDirection.includes("row")
        ) {
          container = p;
          break;
        }
        p = p.parentElement;
      }
      if (!container) {
        container = primaryBtn.parentElement;
      }
    }

    if (container) {
      // Find the direct child of container that contains or is the first found button
      for (const child of container.children) {
        if (foundButtons.some((b) => b === child || child.contains(b))) {
          referenceNode = child;
          break;
        }
      }
      if (!referenceNode) {
        referenceNode = container.firstElementChild;
      }
    }

    return container ? { container, referenceNode } : null;
  }

  // Ensure BS score badge matches the exact rendered height and top-left placement of neighboring action buttons
  function syncBadgeVerticalAlignment(wrapper, badge, target) {
    if (!badge || !target || !target.container) return;

    const ref = target.referenceNode || target.container.firstElementChild;
    if (!ref) return;

    let candidateBtn = null;
    if (
      typeof ref.matches === "function" &&
      ref.matches('[role="button"], button, a')
    ) {
      candidateBtn = ref;
    } else if (typeof ref.querySelector === "function") {
      candidateBtn = ref.querySelector('[role="button"], button, a') || ref;
    } else {
      candidateBtn = ref;
    }

    if (!candidateBtn || candidateBtn === badge) {
      candidateBtn = target.container.querySelector(
        '[data-testid="userActions"], [data-testid="sendDMFromProfile"], [data-testid$="-follow"], [data-testid$="-unfollow"], [data-testid="editProfileButton"], [role="button"], button',
      );
    }

    if (!candidateBtn || candidateBtn === badge) return;

    const btnRect = candidateBtn.getBoundingClientRect();
    if (!btnRect || btnRect.height <= 0) return;

    // Match the exact rendered height of the other buttons (typically 32px or 34px)
    const targetHeight = Math.round(btnRect.height);
    if (targetHeight >= 20 && targetHeight <= 60) {
      badge.style.setProperty("height", `${targetHeight}px`, "important");
      badge.style.setProperty("min-height", `${targetHeight}px`, "important");
      badge.style.setProperty("max-height", `${targetHeight}px`, "important");
    }

    // Both wrapper and badge are anchored to the TOP-LEFT corner of each flex box
    if (wrapper) {
      wrapper.style.setProperty("display", "flex", "important");
      wrapper.style.setProperty("flex-direction", "column", "important");
      wrapper.style.setProperty("align-self", "flex-start", "important");
      wrapper.style.setProperty("align-items", "flex-start", "important");
      wrapper.style.setProperty("justify-content", "flex-start", "important");
      wrapper.style.setProperty("border", "none", "important");
      wrapper.style.setProperty("outline", "none", "important");
      wrapper.style.setProperty("background", "transparent", "important");
      wrapper.style.setProperty("box-shadow", "none", "important");
      wrapper.style.setProperty("margin-top", "0px", "important");
      wrapper.style.setProperty("margin-bottom", "0px", "important");
    }

    badge.style.setProperty("align-self", "flex-start", "important");
    badge.style.setProperty("margin-top", "0px", "important");
    badge.style.setProperty("margin-bottom", "0px", "important");
    badge.style.setProperty("vertical-align", "top", "important");

    // Dynamic top-edge alignment check: ensure badge.top matches neighboring button.top
    const badgeRect = badge.getBoundingClientRect();
    if (badgeRect && badgeRect.height > 0) {
      const diff = Math.round(btnRect.top - badgeRect.top);
      if (Math.abs(diff) >= 1 && Math.abs(diff) < 40) {
        if (wrapper) {
          wrapper.style.setProperty("margin-top", `${diff}px`, "important");
        } else {
          badge.style.setProperty("margin-top", `${diff}px`, "important");
        }
      }
    }
  }

  function removeExistingBadges() {
    document
      .querySelectorAll(".bs-meter-badge-wrapper, .bs-meter-profile-badge")
      .forEach((el) => el.remove());
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
      console.warn(
        "[BS Meter] API Address not configured yet. Please open the BS Meter popup and enter your API URL.",
      );
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
                console.warn(
                  "[BS Meter] Background worker error:",
                  chrome.runtime.lastError.message,
                );
                resolve(null);
              } else {
                resolve(res);
              }
            },
          );
        });

        if (
          response &&
          response.success &&
          response.data &&
          response.data.results
        ) {
          const result = response.data.results[handle];
          if (result) {
            console.info(
              `[BS Meter] Evaluation for @${handle}: status=${result.status}, score=${result.bs_index}`,
            );
            return result;
          }
        } else if (response && !response.success) {
          console.warn("[BS Meter] API error response:", response.error);
        }
      } catch (err) {
        if (
          err.message &&
          err.message.includes("Extension context invalidated")
        ) {
          console.warn(
            "[BS Meter] Extension updated. Please refresh this page (Cmd+Shift+R) to reconnect.",
          );
          return null;
        }
        console.warn("[BS Meter] Background request failed:", err);
      }
    } else {
      console.warn(
        "[BS Meter] Extension context disconnected. Please refresh the page.",
      );
    }

    return null;
  }

  function renderProfileBadge(handle, data) {
    if (!handle) return;

    const target = findProfileActionsTarget();
    let parent = null;
    let refNode = null;

    if (target && target.container) {
      parent = target.container;
      refNode = target.referenceNode;
    } else {
      parent = findProfileHeaderElement();
    }

    if (!parent || !document.contains(parent)) return;

    let wrapper = document.querySelector(
      `.bs-meter-badge-wrapper[data-handle="${handle}"]`,
    );
    let badge = document.querySelector(
      `.bs-meter-profile-badge[data-handle="${handle}"]`,
    );

    if (!wrapper) {
      wrapper = document.createElement("div");
      wrapper.className = "bs-meter-badge-wrapper";
      wrapper.setAttribute("data-handle", handle);
    }

    if (!badge) {
      badge = document.createElement("div");
      badge.className = "bs-meter-profile-badge";
      badge.setAttribute("data-handle", handle);

      badge.addEventListener("mouseenter", () => {
        if (!badge._bsMeterData || badge._bsMeterData.status !== "ready") {
          return;
        }
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
        if (!badge._bsMeterData || badge._bsMeterData.status !== "ready") {
          return;
        }
        isOverlayPinned = !isOverlayPinned;
        if (isOverlayPinned) {
          showOverlay(badge, handle, badge._bsMeterData);
        } else {
          hideOverlay(true);
        }
      });

      console.info(
        `[BS Meter] Attached badge next to profile action buttons for @${handle}`,
      );
    }

    // Ensure badge is mounted inside its flex box wrapper
    if (badge.parentElement !== wrapper) {
      wrapper.appendChild(badge);
    }
    wrapper.className = "bs-meter-badge-wrapper";

    // Attach or re-position wrapper in the actions row next to the other flex boxes
    if (
      wrapper.parentElement !== parent ||
      (refNode && wrapper.nextElementSibling !== refNode)
    ) {
      if (refNode && refNode !== wrapper && parent.contains(refNode)) {
        parent.insertBefore(wrapper, refNode);
      } else if (!parent.contains(wrapper)) {
        parent.appendChild(wrapper);
      }
    }

    if (target && target.container) {
      syncBadgeVerticalAlignment(wrapper, badge, target);
      if (typeof requestAnimationFrame === "function") {
        requestAnimationFrame(() =>
          syncBadgeVerticalAlignment(wrapper, badge, target),
        );
      }
    }

    badge._bsMeterData = data;
    const status = data ? data.status : "queued";
    const level = data && data.badge_level ? data.badge_level : "pending";
    const score =
      data && typeof data.bs_index === "number"
        ? Math.round(data.bs_index)
        : null;

    let labelHtml = `${lucideIcon("loader-2", 13, "bs-spin")} <span>BS: Analyzing...</span>`;
    if (status === "ready" && score !== null) {
      if (level === "low")
        labelHtml = `${lucideIcon("check-circle-2", 13)} <span>BS: ${score}</span>`;
      else if (level === "medium")
        labelHtml = `${lucideIcon("alert-triangle", 13)} <span>BS: ${score}</span>`;
      else
        labelHtml = `${lucideIcon("alert-octagon", 13)} <span>BS: ${score}</span>`;
    } else if (status === "analyzing") {
      labelHtml = `${lucideIcon("loader-2", 13, "bs-spin")} <span>BS: Verifying...</span>`;
    }

    const theme = getActiveTheme();
    badge.setAttribute("data-bs-theme", theme);
    wrapper.setAttribute("data-bs-theme", theme);
    badge.className = `bs-meter-profile-badge bs-badge-${level}`;
    badge.innerHTML = labelHtml;

    const overlay = document.getElementById("bs-meter-global-overlay");
    if (overlay && overlay.classList.contains("visible")) {
      if (status === "ready") {
        showOverlay(badge, handle, data);
      } else {
        hideOverlay(true);
      }
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

      if (pollAttempts > 35) {
        // 35 * 3s = 105s timeout
        console.warn(`[BS Meter] Polling timeout reached for @${handle}`);
        clearInterval(pollingInterval);
        return;
      }

      const updated = await fetchProfileBSIndex(handle);
      if (updated && currentProfileHandle === handle) {
        currentProfileData = updated;
        renderProfileBadge(handle, updated);

        if (updated.status === "ready") {
          console.info(
            `[BS Meter] Analysis COMPLETE for @${handle}! BS Index: ${updated.bs_index}`,
          );
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
        console.info(
          `[BS Meter] Left profile page (@${currentProfileHandle}). Cleaning up.`,
        );
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
        "color: #10b981; font-weight: bold; font-size: 14px;",
      );
      currentProfileHandle = handle;
      currentProfileData = { status: "queued", badge_level: "pending" };
      removeExistingBadges();

      // Mount pending badge immediately
      renderProfileBadge(handle, currentProfileData);

      // Trigger fetch and begin polling
      fetchProfileBSIndex(handle).then((data) => {
        if (currentProfileHandle === handle) {
          if (data) {
            currentProfileData = data;
            renderProfileBadge(handle, data);

            if (data.status === "ready") {
              console.info(
                `[BS Meter] Instant cached evaluation ready for @${handle}`,
              );
              return;
            }
          }
          // Start polling for async pipeline completion
          startPolling(handle);
        }
      });
    }

    // Reattach or reposition badge if React re-rendered or action buttons loaded
    const badge = document.querySelector(
      `.bs-meter-profile-badge[data-handle="${handle}"]`,
    );
    const wrapper = document.querySelector(
      `.bs-meter-badge-wrapper[data-handle="${handle}"]`,
    );
    const target = findProfileActionsTarget();
    if (
      !badge ||
      !wrapper ||
      (target && wrapper.parentElement !== target.container)
    ) {
      renderProfileBadge(
        handle,
        currentProfileData || { status: "queued", badge_level: "pending" },
      );
    } else if (badge && target) {
      syncBadgeVerticalAlignment(wrapper, badge, target);
    }
  }

  // Dismiss pinned overlay on outside click
  document.addEventListener("click", (e) => {
    const overlay = document.getElementById("bs-meter-global-overlay");
    const badge = document.querySelector(".bs-meter-profile-badge");
    if (overlay && overlay.classList.contains("visible")) {
      if (
        !overlay.contains(e.target) &&
        (!badge || !badge.contains(e.target))
      ) {
        hideOverlay(true);
      }
    }
  });

  // Reposition overlay on window resize or scroll
  window.addEventListener(
    "scroll",
    () => {
      const overlay = document.getElementById("bs-meter-global-overlay");
      const badge = document.querySelector(".bs-meter-profile-badge");
      if (overlay && overlay.classList.contains("visible") && badge) {
        positionOverlay(overlay, badge);
      }
    },
    { passive: true },
  );

  window.addEventListener("resize", () => {
    const overlay = document.getElementById("bs-meter-global-overlay");
    const badge = document.querySelector(".bs-meter-profile-badge");
    const wrapper = document.querySelector(".bs-meter-badge-wrapper");
    if (overlay && overlay.classList.contains("visible") && badge) {
      positionOverlay(overlay, badge);
    }
    if (badge) {
      const target = findProfileActionsTarget();
      if (target) syncBadgeVerticalAlignment(wrapper, badge, target);
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
      const badge = document.querySelector(
        `.bs-meter-profile-badge[data-handle="${currentProfileHandle}"]`,
      );
      const wrapper = document.querySelector(
        `.bs-meter-badge-wrapper[data-handle="${currentProfileHandle}"]`,
      );
      const target = findProfileActionsTarget();
      if (
        !badge ||
        !wrapper ||
        (target && wrapper.parentElement !== target.container)
      ) {
        renderProfileBadge(
          currentProfileHandle,
          currentProfileData || { status: "queued", badge_level: "pending" },
        );
      } else if (badge && target) {
        syncBadgeVerticalAlignment(wrapper, badge, target);
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
