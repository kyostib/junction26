document.addEventListener("DOMContentLoaded", () => {
  const apiUrlInput = document.getElementById("apiUrl");
  const enabledCheckbox = document.getElementById("enabledCheckbox");
  const saveBtn = document.getElementById("saveBtn");
  const statusDot = document.getElementById("statusDot");

  // Load existing settings
  chrome.storage.sync.get(["apiBaseUrl", "enabled"], (res) => {
    if (res.apiBaseUrl) apiUrlInput.value = res.apiBaseUrl;
    if (typeof res.enabled === "boolean") enabledCheckbox.checked = res.enabled;
    testConnection(apiUrlInput.value);
  });

  async function testConnection(url) {
    statusDot.className = "status-dot";
    try {
      const cleanUrl = url.replace(/\/+$/, "");
      const res = await fetch(`${cleanUrl}/health`, { signal: AbortSignal.timeout(3000) });
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

    chrome.storage.sync.set(
      {
        apiBaseUrl: url,
        enabled: isEnabled,
      },
      () => {
        saveBtn.innerText = "Saved!";
        setTimeout(() => {
          saveBtn.innerText = "Save & Test Connection";
        }, 1200);
        testConnection(url);
      }
    );
  });
});
