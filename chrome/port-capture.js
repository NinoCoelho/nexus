(function () {
  const meta = document.querySelector('meta[name="nexus-server"]');
  if (!meta) return;
  const port = String(location.port || "");
  if (!port) return;
  chrome.storage.local.get("nexusPort", (data) => {
    if (data.nexusPort !== port) {
      chrome.storage.local.set({ nexusPort: port });
    }
  });
})();
