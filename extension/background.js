// BS Meter - Background Service Worker (Manifest V3)
console.log(
  "%c[BS Meter Background] Service worker active.",
  "color: #38bdf8; font-weight: bold;",
);

chrome.runtime.onInstalled.addListener(() => {
  console.log("[BS Meter Background] Extension installed / updated.");
});

// In-memory cache for API URL to avoid repeated chrome.storage reads
let cachedApiUrl = null;

function updateCachedUrl(rawUrl) {
  if (typeof rawUrl === "string") {
    cachedApiUrl = rawUrl.trim().replace(/\/+$/, "");
  }
}

// Preload API URL on worker startup
chrome.storage.local.get(["apiBaseUrl"], (local) => {
  if (local && local.apiBaseUrl) {
    updateCachedUrl(local.apiBaseUrl);
  } else if (chrome.storage.sync) {
    chrome.storage.sync.get(["apiBaseUrl"], (sync) => {
      if (sync && sync.apiBaseUrl) {
        updateCachedUrl(sync.apiBaseUrl);
      }
    });
  }
});

// Invalidate / update cached URL on storage change
chrome.storage.onChanged.addListener((changes) => {
  if (changes.apiBaseUrl) {
    updateCachedUrl(changes.apiBaseUrl.newValue || "");
  }
});

async function resolveApiUrl(fallbackUrl = "") {
  if (cachedApiUrl) return cachedApiUrl;

  try {
    const local = await chrome.storage.local.get(["apiBaseUrl"]);
    if (local && local.apiBaseUrl) {
      updateCachedUrl(local.apiBaseUrl);
      return cachedApiUrl;
    }
    if (chrome.storage.sync) {
      const sync = await chrome.storage.sync
        .get(["apiBaseUrl"])
        .catch(() => ({}));
      if (sync && sync.apiBaseUrl) {
        updateCachedUrl(sync.apiBaseUrl);
        return cachedApiUrl;
      }
    }
  } catch {
    // ignore storage access errors
  }

  const cleanFallback = (fallbackUrl || "").trim().replace(/\/+$/, "");
  if (cleanFallback) {
    updateCachedUrl(cleanFallback);
  }
  return cachedApiUrl || cleanFallback;
}

// In-memory cache for GET_ANALYSIS reports (TTL 3 minutes)
const analysisCache = new Map();
const ANALYSIS_CACHE_TTL = 3 * 60 * 1000;

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (!request || !request.type) return;

  if (request.type === "BATCH_LOOKUP") {
    (async () => {
      try {
        const apiUrl = await resolveApiUrl(request.apiUrl);

        console.log(
          `[BS Meter Background] Target API URL: "${apiUrl}" for handles:`,
          request.usernames,
        );

        if (!apiUrl) {
          sendResponse({
            success: false,
            error:
              "API address not configured. Please click the BS Meter extension icon and enter your API URL.",
          });
          return;
        }

        const response = await fetch(`${apiUrl}/api/v1/overlay/twitter/batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ usernames: request.usernames }),
        });

        if (!response.ok) {
          const errText = await response.text();
          console.warn(
            "[BS Meter Background] API HTTP error:",
            response.status,
            errText,
          );
          sendResponse({
            success: false,
            status: response.status,
            error: errText,
          });
          return;
        }

        const data = await response.json();
        console.log(
          "[BS Meter Background] API response successfully received:",
          data,
        );
        sendResponse({ success: true, data: data });
      } catch (err) {
        console.error(
          `[BS Meter Background] Network fetch failed: ${err.message}`,
        );
        sendResponse({ success: false, error: err.message || String(err) });
      }
    })();

    return true; // Keep channel open for asynchronous sendResponse
  }

  if (request.type === "GET_ANALYSIS") {
    (async () => {
      try {
        const reportUrl = request.reportUrl || "";
        const now = Date.now();

        // Check memory cache first
        if (reportUrl && analysisCache.has(reportUrl)) {
          const cached = analysisCache.get(reportUrl);
          if (now - cached.timestamp < ANALYSIS_CACHE_TTL) {
            sendResponse({ success: true, data: cached.data });
            return;
          }
          analysisCache.delete(reportUrl);
        }

        const apiUrl = await resolveApiUrl(request.apiUrl);
        if (!apiUrl && !reportUrl.startsWith("http")) {
          sendResponse({ success: false, error: "No API URL configured" });
          return;
        }

        const endpoint = reportUrl.startsWith("http")
          ? reportUrl
          : `${apiUrl}${reportUrl}`;

        const response = await fetch(endpoint);
        if (!response.ok) {
          sendResponse({ success: false, status: response.status });
          return;
        }

        const data = await response.json();

        // Cache report in memory
        if (reportUrl) {
          if (analysisCache.size > 50) {
            const oldestKey = analysisCache.keys().next().value;
            analysisCache.delete(oldestKey);
          }
          analysisCache.set(reportUrl, { data, timestamp: now });
        }

        sendResponse({ success: true, data: data });
      } catch (err) {
        console.error(
          `[BS Meter Background] Analysis fetch failed: ${err.message}`,
        );
        sendResponse({ success: false, error: err.message || String(err) });
      }
    })();

    return true;
  }
});
