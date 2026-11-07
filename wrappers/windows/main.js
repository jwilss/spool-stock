const {app, BrowserWindow, shell, session, ipcMain} = require("electron");
const path = require("path");
const {writePresets} = require("./orca");
const SITE = "https://jwilss.github.io/spool-stock/";
function open() {
  const win = new BrowserWindow({width: 1200, height: 820, autoHideMenuBar: true, backgroundColor: "#101518", title: "Spool Stock",
    webPreferences: {preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true}});
  win.webContents.setWindowOpenHandler(({url}) => { shell.openExternal(url); return {action: "deny"}; });
  win.loadURL(SITE);
}
ipcMain.handle("orca-write", (event, files) => {
  const from = event.senderFrame && event.senderFrame.url;
  if (!from || !from.startsWith(SITE)) return {error: "Not allowed from this page."};
  try { return writePresets(app.getPath("appData"), files); }
  catch (e) { return {error: "Couldn't write to Orca's folder: " + e.message}; }
});
app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((wc, permission, done) => done(permission === "media"));
  open();
});
app.on("window-all-closed", () => app.quit());
