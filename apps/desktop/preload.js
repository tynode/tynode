const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('tynode', { setTheme: t => ipcRenderer.send('theme', t) });