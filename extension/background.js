// BS Meter - Background Service Worker (Manifest V3)
console.log("%c[BS Meter Background] Service worker active.", "color: #38bdf8; font-weight: bold;");

chrome.runtime.onInstalled.addListener(() => {
  console.log("[BS Meter Background] Extension installed / updated.");
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request && request.type === "BATCH_LOOKUP") {
    (async () => {
      try {
        let apiUrl = (request.apiUrl || "").trim();

        // If not passed in message, check storage
        if (!apiUrl) {
          const local = await chrome.storage.local.get(["apiBaseUrl"]);
          if (local && local.apiBaseUrl) {
            apiUrl = local.apiBaseUrl;
          } else if (chrome.storage.sync) {
            const sync = await chrome.storage.sync.get(["apiBaseUrl"]);
            if (sync && sync.apiBaseUrl) apiUrl = sync.apiBaseUrl;
          }
        }

        apiUrl = (apiUrl || "http://localhost:8000").replace(/\/+$/, "");
        console.log("[BS Meter Background] Proxying batch evaluation for:", request.usernames, "to", apiUrl);

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
});
