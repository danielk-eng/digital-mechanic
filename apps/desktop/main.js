const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");

const DEFAULT_URL = "https://digital-mechanic-iikuznitsa.vercel.app";

function normalize(raw) {
  let u = String(raw || "").trim();
  if (!u) return DEFAULT_URL;
  if (!/^https?:\/\//i.test(u)) u = "https://" + u;
  try {
    const p = new URL(u);
    if (p.protocol !== "http:" && p.protocol !== "https:") return DEFAULT_URL;
    return (p.origin + (p.pathname === "/" ? "" : p.pathname) + p.search).replace(/\/$/, "");
  } catch {
    return DEFAULT_URL;
  }
}

function configPath() {
  return path.join(app.getPath("userData"), "server-url.txt");
}

function readFileUrl(file) {
  try {
    if (fs.existsSync(file)) {
      const t = fs.readFileSync(file, "utf8").trim();
      if (t) return normalize(t);
    }
  } catch {
    /* ignore */
  }
  return null;
}

function getServerUrl() {
  const arg = process.argv.find((a) => a.startsWith("--server-url="));
  if (arg) return normalize(arg.slice("--server-url=".length));
  if (process.env.MECHANIC_SERVER_URL) return normalize(process.env.MECHANIC_SERVER_URL);
  const sidecar =
    readFileUrl(path.join(path.dirname(process.execPath), "server-url.txt")) ||
    readFileUrl(path.join(process.cwd(), "server-url.txt"));
  if (sidecar) return sidecar;
  return readFileUrl(configPath()) || DEFAULT_URL;
}

function setServerUrl(url) {
  const u = normalize(url);
  fs.mkdirSync(app.getPath("userData"), { recursive: true });
  fs.writeFileSync(configPath(), u + "\n", "utf8");
  return u;
}

function isOurOrigin(url, serverUrl) {
  try {
    return new URL(url).origin === new URL(serverUrl || getServerUrl()).origin;
  } catch {
    return false;
  }
}

function grantMediaForOurOrigin(permission, requestingUrl) {
  const media =
    permission === "media" || permission === "microphone" || permission === "camera";
  return media && isOurOrigin(requestingUrl, getServerUrl());
}

function attachPermissionHandlers(ses) {
  ses.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const url =
      (details && details.requestingUrl) ||
      (webContents && webContents.getURL()) ||
      "";
    callback(grantMediaForOurOrigin(permission, url));
  });
  ses.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
    return grantMediaForOurOrigin(permission, requestingOrigin);
  });
}

let mainWindow = null;

function loadStand(win) {
  const url = getServerUrl();
  win.loadURL(url).catch(() => {
    win.loadFile(path.join(__dirname, "offline.html"));
  });
}

function promptUrl(parent, current) {
  return new Promise((resolve) => {
    const child = new BrowserWindow({
      parent,
      modal: true,
      width: 500,
      height: 220,
      resizable: false,
      minimizable: false,
      maximizable: false,
      backgroundColor: "#0e1013",
      title: "Адрес сервера",
      autoHideMenuBar: true,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
      },
    });
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (!child.isDestroyed()) child.close();
      resolve(value);
    };
    const onSubmit = (e, url) => {
      if (e.sender !== child.webContents) return;
      finish(url ? normalize(url) : null);
    };
    const onCancel = (e) => {
      if (e.sender !== child.webContents) return;
      finish(null);
    };
    ipcMain.on("prompt-submit", onSubmit);
    ipcMain.on("prompt-cancel", onCancel);
    child.on("closed", () => {
      ipcMain.removeListener("prompt-submit", onSubmit);
      ipcMain.removeListener("prompt-cancel", onCancel);
      finish(null);
    });
    child.loadFile(path.join(__dirname, "prompt.html"), { query: { url: current } });
  });
}

async function changeServer(win) {
  const current = getServerUrl();
  const choice = await dialog.showMessageBox(win, {
    type: "question",
    title: "Адрес сервера",
    message: "Стенд, к которому подключается приложение",
    detail:
      "Текущий адрес: " +
      current +
      "\n\nЕго также можно задать флагом --server-url=, переменной MECHANIC_SERVER_URL или файлом server-url.txt рядом с программой.",
    buttons: ["Изменить…", "Сбросить на Vercel", "Отмена"],
    defaultId: 0,
    cancelId: 2,
    noLink: true,
  });
  if (choice.response === 2) return;
  if (choice.response === 1) {
    setServerUrl(DEFAULT_URL);
    loadStand(win);
    return;
  }
  const url = await promptUrl(win, current);
  if (!url) return;
  setServerUrl(url);
  loadStand(win);
}

function buildMenu(win) {
  const template = [
    {
      label: "Файл",
      submenu: [
        {
          label: "Адрес сервера…",
          accelerator: "CmdOrCtrl+,",
          click: () => changeServer(win),
        },
        { label: "Обновить", role: "reload", accelerator: "CmdOrCtrl+R" },
        { type: "separator" },
        { label: "Выход", role: "quit" },
      ],
    },
    {
      label: "Правка",
      submenu: [
        { label: "Отменить", role: "undo" },
        { label: "Повторить", role: "redo" },
        { type: "separator" },
        { label: "Вырезать", role: "cut" },
        { label: "Копировать", role: "copy" },
        { label: "Вставить", role: "paste" },
        { label: "Выделить всё", role: "selectAll" },
      ],
    },
    {
      label: "Вид",
      submenu: [
        { label: "Сброс масштаба", role: "resetZoom" },
        { label: "Крупнее", role: "zoomIn" },
        { label: "Мельче", role: "zoomOut" },
        { type: "separator" },
        { label: "Полный экран", role: "togglefullscreen" },
        { type: "separator" },
        { label: "Инструменты разработчика", role: "toggleDevTools" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 390,
    minHeight: 640,
    backgroundColor: "#0e1013",
    title: "Цифровой механик",
    icon: path.join(__dirname, "assets", "icon.png"),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  mainWindow = win;
  attachPermissionHandlers(win.webContents.session);
  win.once("ready-to-show", () => win.show());
  win.webContents.on("did-fail-load", (_e, code, _desc, _url, isMain) => {
    if (isMain && code !== -3) {
      win.loadFile(path.join(__dirname, "offline.html"));
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const p = new URL(url);
      if (p.protocol === "http:" || p.protocol === "https:") shell.openExternal(url);
    } catch {
      /* ignore */
    }
    return { action: "deny" };
  });
  win.loadFile(path.join(__dirname, "splash.html")).then(() => {
    setTimeout(() => loadStand(win), 400);
  });
  buildMenu(win);
  return win;
}

app.setName("Цифровой механик");

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(() => {
    ipcMain.handle("get-server-url", () => getServerUrl());
    ipcMain.handle("set-server-url", (_e, url) => {
      if (!url) return getServerUrl();
      const u = setServerUrl(url);
      if (mainWindow) loadStand(mainWindow);
      return u;
    });
    ipcMain.handle("open-settings", () => {
      if (mainWindow) return changeServer(mainWindow);
    });
    ipcMain.handle("reload-stand", () => {
      if (mainWindow) loadStand(mainWindow);
    });
    createWindow();
  });
  app.on("window-all-closed", () => app.quit());
}
