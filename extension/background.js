// BS Meter - Background Service Worker (Manifest V3)
console.log("%c[BS Meter Background] Service worker active.", "color: #38bdf8; font-weight: bold;");

chrome.runtime.onInstalled.addListener(() => {
  console.log("[BS Meter Background] Extension installed / updated.");
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request && request.type === "BATCH_LOOKUP") {
    (async () => {
      try {
        // ALWAYS check storage first so user's configured URL takes precedence
        const local = await chrome.storage.local.get(["apiBaseUrl"]);
        let apiUrl = (local && local.apiBaseUrl ? local.apiBaseUrl : "").trim();

        if (!apiUrl && chrome.storage.sync) {
          const sync = await chrome.storage.sync.get(["apiBaseUrl"]).catch(() => ({}));
          if (sync && sync.apiBaseUrl) apiUrl = sync.apiBaseUrl.trim();
        }

        // Fallback to request parameter only if storage is completely empty
        if (!apiUrl) {
          apiUrl = (request.apiUrl || "").trim();
        }

        apiUrl = apiUrl.replace(/\/+$/, "");

        console.log(`[BS Meter Background] Target API URL: "${apiUrl}" for handles:`, request.usernames);

        if (!apiUrl) {
          sendResponse({
            success: false,
            error: "API address not configured. Please click the BS Meter extension icon and enter your API URL."
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
          console.warn("[BS Meter Background] API HTTP error:", response.status, errText);
          sendResponse({ success: false, status: response.status, error: errText });
          return;
        }

        const data = await response.json();
        console.log("[BS Meter Background] API response successfully received:", data);
        sendResponse({ success: true, data: data });
      } catch (err) {
        console.error(`[BS Meter Background] Network fetch failed: ${err.message}`);
        sendResponse({ success: false, error: err.message || String(err) });
      }
    })();

    return true; // Keep channel open for asynchronous sendResponse
  }

  if (request && request.type === "GET_ANALYSIS") {
    (async () => {
      try {
        const local = await chrome.storage.local.get(["apiBaseUrl"]);
        let apiUrl = (local && local.apiBaseUrl ? local.apiBaseUrl : request.apiUrl || "").trim().replace(/\/+$/, "");
        if (!apiUrl && chrome.storage.sync) {
          const sync = await chrome.storage.sync.get(["apiBaseUrl"]).catch(() => ({}));
          if (sync && sync.apiBaseUrl) apiUrl = sync.apiBaseUrl.trim().replace(/\/+$/, "");
        }
        if (!apiUrl) {
          sendResponse({ success: false, error: "No API URL configured" });
          return;
        }
        const endpoint = (request.reportUrl || "").startsWith("http") ? request.reportUrl : `${apiUrl}${request.reportUrl}`;
        const response = await fetch(endpoint);
        if (!response.ok) {
          sendResponse({ success: false, status: response.status });
          return;
        }
        const data = await response.json();
        sendResponse({ success: true, data: data });
      } catch (err) {
        console.error(`[BS Meter Background] Analysis fetch failed: ${err.message}`);
        sendResponse({ success: false, error: err.message || String(err) });
      }
    })();

    return true;
  }
});
