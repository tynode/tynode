const { app, BrowserWindow, Menu, ipcMain, shell } = require('electron');
const cp = require('child_process');
const spawnSync = cp.spawnSync;
const spawn = (cmd, args, opts) => cp.spawn(cmd, args, app.isPackaged ? Object.assign({}, opts, { cwd: DATA }) : opts);
const http = require('http');
const path = require('path');
const fs = require('fs');
const DATA = path.join(process.env.LOCALAPPDATA || app.getPath('appData'), 'Tynode');
try { fs.mkdirSync(DATA, { recursive: true }); } catch (e) {}
app.setPath('userData', DATA);

const PORT = 8799;
const URL = 'http://127.0.0.1:' + PORT + '/';
const ICON = path.join(__dirname, '..', 'console', 'static', 'logo.png');
const SPLASH = path.join(__dirname, 'splash.html');
const LOG = path.join(DATA, 'engine.log');
let server = null;
let win = null;

function killServer() {
  if (!server) return;
  try { spawnSync('taskkill', ['/pid', String(server.pid), '/T', '/F'], { windowsHide: true }); } catch (e) {}
  server = null;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

function probe() {
  return new Promise(resolve => {
    const req = http.get(URL + 'v1/auth/me', { timeout: 1500 }, res => { res.resume(); resolve(res.statusCode); });
    req.on('timeout', () => { req.destroy(); resolve(0); });
    req.on('error', () => resolve(0));
  });
}

function readPublicCfg() {
  try {
    const j = JSON.parse(require('fs').readFileSync(path.join(process.resourcesPath, 'supabase.public.json'), 'utf8'));
    return { SUPABASE_URL: j.url, SUPABASE_ANON_KEY: j.anon };
  } catch (e) { return {}; }
}function pythonCandidates() {
  if (app.isPackaged) {
    try { require('fs').mkdirSync(DATA, { recursive: true }); } catch (e) {}
    return [{ cmd: path.join(process.resourcesPath, 'engine', 'tynode-engine.exe'), pre: [] }];
  }
  return pythonCandidatesDev();
}
function pythonCandidatesDev() {
  const list = [];
  const venvPy = path.join(__dirname, '..', '..', '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPy)) list.push({ cmd: venvPy, pre: [] });
  list.push({ cmd: 'python', pre: [] });
  list.push({ cmd: 'py', pre: ['-3'] });
  return list;
}

async function ensureServer() {
  if ((await probe()) === 401) return true;
  const consoleDir = path.join(__dirname, '..', 'console');
  try { fs.writeFileSync(LOG, 'Tynode engine log ' + new Date().toISOString() + '\n'); } catch (e) {}
  for (const c of pythonCandidates()) {
    let dead = false;
    let out = 'ignore';
    try { out = fs.openSync(LOG, 'a'); fs.writeSync(out, '\n--- trying: ' + c.cmd + ' ---\n'); } catch (e) {}
    const proc = spawn(c.cmd, c.pre.concat(['-m', 'uvicorn', 'server:app', '--port', String(PORT), '--log-level', 'warning']),
      { cwd: consoleDir, windowsHide: true, env: Object.assign({}, process.env, Object.assign({ TYNODE_DATA_DIR: DATA }, readPublicCfg())), stdio: ['ignore', out, out] });
    proc.on('exit', () => { dead = true; });
    proc.on('error', () => { dead = true; });
    server = proc;
    for (let i = 0; i < 40; i++) {
      if ((await probe()) === 401) return true;
      if (dead) break;
      await sleep(300);
    }
    try { proc.kill(); } catch (e) {}
    server = null;
  }
  return false;
}

function createWindow() {
  Menu.setApplicationMenu(null);
  win = new BrowserWindow({
    width: 1280, height: 820, minWidth: 1000, minHeight: 640,
    backgroundColor: '#0e0b20', show: false, title: 'Tynode',
    icon: fs.existsSync(ICON) ? ICON : undefined,
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#0e0b20', symbolColor: '#e5e5ea', height: 46 },
    webPreferences: { contextIsolation: true, nodeIntegration: false, preload: path.join(__dirname, 'preload.js') }
  });
  win.once('ready-to-show', () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https:\/\//i.test(url)) shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('before-input-event', (e, i) => {
    if (i.type !== 'keyDown') return;
    const k = String(i.key).toLowerCase();
    if (k === 'f12' || (i.control && i.shift && k === 'i')) win.webContents.toggleDevTools();
    else if (k === 'f5' || (i.control && k === 'r')) win.webContents.reloadIgnoringCache();
  });
  win.webContents.on('did-fail-load', (e, code, desc, url, isMain) => {
    if (isMain && code !== -3 && String(url).startsWith('http')) win.loadFile(SPLASH, { query: { error: '1' } });
  });
  win.on('closed', () => { win = null; });
  win.loadFile(SPLASH);
}

ipcMain.on('theme', (e, t) => {
  if (!win) return;
  const dark = t !== 'light';
  win.setBackgroundColor(dark ? '#0e0b20' : '#f4f5fa');
  if (typeof win.setTitleBarOverlay === 'function') {
    win.setTitleBarOverlay({ color: dark ? '#0e0b20' : '#f4f5fa', symbolColor: dark ? '#e5e5ea' : '#1c1c28', height: 46 });
  }
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(async () => {
    createWindow();
    const [ok] = await Promise.all([ensureServer(), sleep(1900)]);
    if (!win) return;
    if (ok) win.loadURL(URL);
    else win.loadFile(SPLASH, { query: { error: '1' } });
  });
  app.on('window-all-closed', () => { killServer(); app.exit(0); });
  app.on('before-quit', killServer);
}