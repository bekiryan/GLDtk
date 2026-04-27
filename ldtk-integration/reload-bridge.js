// ── GLDtk reload bridge ───────────────────────────────────────────────────
// Appended to LDtk's app/assets/main.js by patch.sh.
// Listens for a reload request from our injected panel and reloads the
// Electron renderer — LDtk will re-open the last project automatically.
(function() {
  var _ipcMain = require("electron").ipcMain;

  // Called by gldtk-panel.js after a level is written to disk.
  _ipcMain.on("gldtk-reload", function(event) {
    var win = event.sender.getOwnerBrowserWindow
      ? event.sender.getOwnerBrowserWindow()
      : null;
    // Fallback: find the first focused BrowserWindow.
    if (!win) {
      var _BrowserWindow = require("electron").BrowserWindow;
      win = _BrowserWindow.getFocusedWindow() || _BrowserWindow.getAllWindows()[0];
    }
    if (win) {
      win.webContents.reloadIgnoringCache();
    }
  });
})();
// ── end GLDtk reload bridge ───────────────────────────────────────────────
