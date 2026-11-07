// Gives the Spool Stock site one narrow ability: sending its filament presets to OrcaSlicer.
const {contextBridge, ipcRenderer} = require("electron");
contextBridge.exposeInMainWorld("spoolStockDesktop", {
  orca: {write: files => ipcRenderer.invoke("orca-write", files)}
});
