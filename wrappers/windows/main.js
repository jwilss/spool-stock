const {app, BrowserWindow, shell, session} = require("electron");
const SITE = "https://jwilss.github.io/spool-stock/";
function open() {
  const win = new BrowserWindow({width: 1200, height: 820, autoHideMenuBar: true, backgroundColor: "#101518", title: "Spool Stock"});
  win.webContents.setWindowOpenHandler(({url}) => { shell.openExternal(url); return {action: "deny"}; });
  win.loadURL(SITE);
}
app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((wc, permission, done) => done(permission === "media"));
  open();
});
app.on("window-all-closed", () => app.quit());
