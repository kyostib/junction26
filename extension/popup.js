document.addEventListener("DOMContentLoaded", () => {
  const apiUrlInput = document.getElementById("apiUrl");
  const enabledCheckbox = document.getElementById("enabledCheckbox");
  const saveBtn = document.getElementById("saveBtn");
  const statusDot = document.getElementById("statusDot");

  // Clean up legacy keys if they exist in storage
  chrome.storage.local
    .remove(["envMode", "devApiUrl", "prodApiUrl"])
    .catch(() => {});
  if (chrome.storage.sync) {
    chrome.storage.sync
      .remove(["envMode", "devApiUrl", "prodApiUrl"])
      .catch(() => {});
  }

  // Load saved settings: check local storage first, then sync
  chrome.storage.local.get(["apiBaseUrl", "enabled"], (res) => {
    if (res && res.apiBaseUrl) {
      apiUrlInput.value = res.apiBaseUrl;
      if (typeof res.enabled === "boolean")
        enabledCheckbox.checked = res.enabled;
      testConnection(res.apiBaseUrl);
    } else {
      chrome.storage.sync.get(["apiBaseUrl", "enabled"], (syncRes) => {
        if (syncRes && syncRes.apiBaseUrl) {
          apiUrlInput.value = syncRes.apiBaseUrl;
          chrome.storage.local.set({ apiBaseUrl: syncRes.apiBaseUrl });
        }
        if (syncRes && typeof syncRes.enabled === "boolean") {
          enabledCheckbox.checked = syncRes.enabled;
        }
        if (apiUrlInput.value) {
          testConnection(apiUrlInput.value);
        }
      });
    }
  });

  let activeHealthController = null;

  async function testConnection(url) {
    if (activeHealthController) {
      activeHealthController.abort();
      activeHealthController = null;
    }

    statusDot.className = "status-dot";
    const cleanUrl = (url || "").trim().replace(/\/+$/, "");
    if (!cleanUrl) {
      statusDot.className = "status-dot offline";
      return;
    }

    activeHealthController = new AbortController();
    const currentController = activeHealthController;

    try {
      const res = await fetch(`${cleanUrl}/health`, {
        signal: currentController.signal,
      });
      if (res.ok) {
        statusDot.className = "status-dot online";
      } else {
        statusDot.className = "status-dot offline";
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        statusDot.className = "status-dot offline";
      }
    } finally {
      if (activeHealthController === currentController) {
        activeHealthController = null;
      }
    }
  }

  async function saveSettings() {
    const rawUrl = apiUrlInput.value.trim();
    const cleanUrl = rawUrl.replace(/\/+$/, "");
    const isEnabled = enabledCheckbox.checked;

    if (cleanUrl) {
      let origin;
      try {
        origin = new URL(cleanUrl).origin;
      } catch {
        statusDot.className = "status-dot offline";
        return;
      }
      if (
        chrome.permissions &&
        !(await chrome.permissions.contains({ origins: [`${origin}/*`] }))
      ) {
        const granted = await chrome.permissions.request({
          origins: [`${origin}/*`],
        });
        if (!granted) return;
      }
    }

    const payload = {
      apiBaseUrl: cleanUrl,
      enabled: isEnabled,
    };

    console.log("[BS Meter Popup] Saving configuration:", payload);

    // Save to local and sync storage immediately
    chrome.storage.local.set(payload, () => {
      if (chrome.storage.sync) {
        chrome.storage.sync
          .set({ apiBaseUrl: cleanUrl, enabled: isEnabled })
          .catch(() => {});
      }

      // Notify targeted tabs running x.com/twitter.com immediately
      if (chrome.tabs && chrome.tabs.query) {
        const notifyTab = (tab) => {
          chrome.tabs
            .sendMessage(tab.id, {
              type: "CONFIG_UPDATED",
              apiBaseUrl: cleanUrl,
              enabled: isEnabled,
            })
            .catch(() => {});
        };

        chrome.tabs.query(
          { url: ["*://*.x.com/*", "*://*.twitter.com/*"] },
          (tabs) => {
            if (tabs && tabs.length > 0) {
              tabs.forEach(notifyTab);
            } else {
              // Fallback query across all tabs if URL pattern matching is restricted
              chrome.tabs.query({}, (fallbackTabs) => {
                for (const tab of fallbackTabs || []) {
                  if (
                    tab.url &&
                    /^https?:\/\/(?:[^/]+\.)?(?:x\.com|twitter\.com)(?:\/|$)/i.test(
                      tab.url,
                    )
                  ) {
                    notifyTab(tab);
                  }
                }
              });
            }
          },
        );
      }

      saveBtn.innerText = "Saved & Applied!";
      saveBtn.style.background = "#10b981";
      saveBtn.style.color = "#ffffff";
      setTimeout(() => {
        saveBtn.innerText = "Save & Apply";
        saveBtn.style.background = "#ffffff";
        saveBtn.style.color = "#000000";
      }, 1500);

      testConnection(cleanUrl);
    });
  }

  saveBtn.addEventListener("click", saveSettings);

  apiUrlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveSettings();
    }
  });

  apiUrlInput.addEventListener("change", saveSettings);
  enabledCheckbox.addEventListener("change", saveSettings);
});
