const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  getServerUrl: () => ipcRenderer.invoke("get-server-url"),
  setServerUrl: (url) => ipcRenderer.invoke("set-server-url", url),
  openSettings: () => ipcRenderer.invoke("open-settings"),
  reload: () => ipcRenderer.invoke("reload-stand"),
  submitPrompt: (url) => ipcRenderer.send("prompt-submit", url),
  cancelPrompt: () => ipcRenderer.send("prompt-cancel"),
});
