document.addEventListener("DOMContentLoaded", () => {
  const apiUrlInput = document.getElementById("apiUrl");
  const enabledCheckbox = document.getElementById("enabledCheckbox");
  const saveBtn = document.getElementById("saveBtn");
  const statusDot = document.getElementById("statusDot");

  // Load saved settings: check local storage first, then sync
  chrome.storage.local.get(["apiBaseUrl", "enabled"], (res) => {
    if (res && res.apiBaseUrl) {
      apiUrlInput.value = res.apiBaseUrl;
      if (typeof res.enabled === "boolean") enabledCheckbox.checked = res.enabled;
      testConnection(res.apiBaseUrl);
    } else {
      chrome.storage.sync.get(["apiBaseUrl", "enabled"], (syncRes) => {
        if (syncRes && syncRes.apiBaseUrl) {
          apiUrlInput.value = syncRes.apiBaseUrl;
        }
        if (syncRes && typeof syncRes.enabled === "boolean") {
          enabledCheckbox.checked = syncRes.enabled;
        }
        testConnection(apiUrlInput.value);
      });
    }
  });

  async function testConnection(url) {
    statusDot.className = "status-dot";
    const cleanUrl = (url || "").trim().replace(/\/+$/, "");
    if (!cleanUrl) {
      statusDot.className = "status-dot offline";
      return;
    }
    try {
      const res = await fetch(`${cleanUrl}/health`, { signal: AbortSignal.timeout(3500) });
      if (res.ok) {
        statusDot.className = "status-dot online";
      } else {
        statusDot.className = "status-dot offline";
      }
    } catch {
      statusDot.className = "status-dot offline";
    }
  }

  saveBtn.addEventListener("click", () => {
    const url = apiUrlInput.value.trim().replace(/\/+$/, "");
    const isEnabled = enabledCheckbox.checked;

    const payload = {
      apiBaseUrl: url,
      enabled: isEnabled,
    };

    // Save to both local and sync storage for instant availability
    chrome.storage.local.set(payload, () => {
      if (chrome.storage.sync) {
        chrome.storage.sync.set(payload).catch(() => {});
      }

      // Notify all active x.com tabs immediately
      chrome.tabs.query({ url: ["*://x.com/*", "*://*.x.com/*", "*://twitter.com/*", "*://*.twitter.com/*"] }, (tabs) => {
        for (const tab of tabs) {
          if (tab.id) {
            chrome.tabs.sendMessage(tab.id, { type: "CONFIG_UPDATED", apiBaseUrl: url, enabled: isEnabled }).catch(() => {});
          }
        }
      });

      saveBtn.innerText = "Saved & Applied!";
      setTimeout(() => {
        saveBtn.innerText = "Save & Test Connection";
      }, 1500);
      testConnection(url);
    });
  });
});
