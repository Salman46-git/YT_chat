// Relays requests from the YouTube page to the local backend.
// Change this if you run the backend on another port/host
// (and update host_permissions in manifest.json to match).
const API_BASE = "http://localhost:8000";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== "api") return;

  fetch(API_BASE + msg.path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(msg.body),
  })
    .then(async (res) => {
      const data = await res.json().catch(() => ({}));
      sendResponse({ ok: res.ok, data });
    })
    .catch(() =>
      sendResponse({
        ok: false,
        data: { detail: `Cannot reach the backend at ${API_BASE}. Is it running?` },
      })
    );

  return true; // keep the channel open for the async response
});
