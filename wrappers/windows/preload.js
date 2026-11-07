// Gives the Spool Stock site one narrow ability: sending its filaments to OrcaSlicer.
const {contextBridge, ipcRenderer} = require("electron");
contextBridge.exposeInMainWorld("spoolStockDesktop", {
  orca: {
    write: (files, opts) => ipcRenderer.invoke("orca-write", files, opts),
    restore: () => ipcRenderer.invoke("orca-restore")
  }
});
