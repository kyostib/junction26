const form = document.querySelector("#scan-form");
const input = document.querySelector("#profile-input");
const button = document.querySelector("#scan-button");
const statusBox = document.querySelector("#scan-status");
const resultBox = document.querySelector("#scan-result");
const HTML_ESCAPE_MAP = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};
API_BASE = "https://api.bsmeter.tech";

document.querySelector(".example").addEventListener("click", () => {
  input.value = "elonmusk";
  input.focus();
});

function setStatus(message, error = false) {
  statusBox.hidden = false;
  statusBox.classList.toggle("error", error);
  statusBox.textContent = message;
}

function formatType(type) {
  return String(type || "signal").replaceAll("_", " ");
}

function renderResult(analysis, handle) {
  const data = analysis.result || {};
  const score = Math.round(data.bs_index ?? data.overall_score ?? 0);
  const level = data.badge_level || "low";
  const flags = Array.isArray(data.top_flags) ? data.top_flags : [];
  resultBox.innerHTML = `
    <div class="result-head"><div><div class="result-handle">@${handle}</div><div class="result-badge">${level.toUpperCase()} SIGNAL · ${data.posts_analyzed ?? 0} POSTS REVIEWED</div></div><div class="result-score">${score}<small>/100</small></div></div>
    <p class="result-summary">${escapeHtml(data.summary || "Analysis complete. Review the signals below and follow the sources to explore the context.")}</p>
    <div class="result-flags">${flags.length ? flags.map((flag) => `<div class="flag"><b>${escapeHtml(formatType(flag.type))} · ${escapeHtml(flag.assessment || "signal")}</b>${escapeHtml(flag.summary || "")}${flag.evidence_url ? ` <a href="${safeUrl(flag.evidence_url)}" target="_blank" rel="noopener noreferrer">Source ↗</a>` : ""}</div>`).join("") : '<div class="flag"><b>No standout flags</b>No top-level signals were returned for this scan.</div>'}</div>
    <div class="result-meta">A product heuristic based on public posts and available web sources.</div>`;
  resultBox.hidden = false;
  statusBox.hidden = true;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char]);
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol)
      ? escapeHtml(url.href)
      : "#";
  } catch {
    return "#";
  }
}

async function waitForAnalysis(id, handle) {
  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const response = await fetch(
      `${API_BASE}/api/v1/analyses/${encodeURIComponent(id)}`,
    );
    if (!response.ok)
      throw new Error(
        "Could not retrieve the analysis status. Please try again.",
      );
    const analysis = await response.json();
    if (analysis.status === "completed") return renderResult(analysis, handle);
    if (analysis.status === "failed")
      throw new Error(
        analysis.error ||
          "The scan could not be completed. Please try again later.",
      );
    setStatus(
      analysis.status === "running"
        ? `Reviewing @${handle}’s recent posts and checking selected claims…`
        : `Your scan for @${handle} is in the queue. This can take a little while…`,
    );
  }
  throw new Error(
    "This scan is taking longer than expected. Please try again in a moment.",
  );
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resultBox.hidden = true;
  button.disabled = true;
  button.textContent = "Starting…";
  setStatus("Starting profile scan…");
  try {
    const response = await fetch(`${API_BASE}/api/v1/analyses`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        platform: "twitter",
        profile_url: input.value.trim(),
      }),
    });
    const payload = await response.json();
    if (!response.ok)
      throw new Error(
        typeof payload.detail === "string"
          ? payload.detail
          : "Please enter a valid X handle or profile link.",
      );
    const handle = input.value
      .trim()
      .replace(/^@/, "")
      .replace(/^https?:\/\/(www\.)?(x|twitter)\.com\//i, "")
      .split(/[/?#]/)[0];
    await waitForAnalysis(payload.analysis_id, handle);
  } catch (error) {
    setStatus(error.message || "Something went wrong. Please try again.", true);
  } finally {
    button.disabled = false;
    button.innerHTML = "Scan profile <span>↗</span>";
  }
});

const extensionLink = document.querySelector("#extension-link");
extensionLink.href = "https://github.com/kyostib/junction26/releases";
