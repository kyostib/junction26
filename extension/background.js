// BS Meter - Background Service Worker
console.log("%c[BS Meter Background] Service worker active.", "color: #38bdf8; font-weight: bold;");

chrome.runtime.onInstalled.addListener(() => {
  console.log("[BS Meter Background] Extension installed / updated.");
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "BATCH_LOOKUP") {
    const apiUrl = (request.apiUrl || "http://localhost:8000").replace(/\/+$/, "");
    console.log("[BS Meter Background] Proxying request from extension origin for handles:", request.usernames);

    fetch(`${apiUrl}/api/v1/overlay/twitter/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ usernames: request.usernames }),
    })
      .then(async (res) => {
        if (!res.ok) {
          const errText = await res.text();
          console.error("[BS Meter Background] API HTTP Error:", res.status, errText);
          sendResponse({ success: false, status: res.status, error: errText });
          return;
        }
        const data = await res.json();
        console.log("[BS Meter Background] Successfully received API response:", data);
        sendResponse({ success: true, data: data });
      })
      .catch((err) => {
        console.error("[BS Meter Background] Fetch failed:", err.message);
        sendResponse({ success: false, error: err.message });
      });

    return true; // Keep channel open for async response
  }
});
