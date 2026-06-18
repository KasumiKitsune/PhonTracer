const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow = null;
let pythonProcess = null;

// Start local Python FastAPI backend as a child process
function startPythonBackend() {
  const isPackaged = app.isPackaged;
  let pythonScript = '';
  let cwd = '';

  if (isPackaged) {
    // In production, we assume the python executable or script is bundled/located next to resources
    // For local running in unpacked state:
    pythonScript = path.join(process.resourcesPath, 'backend', 'main.py');
    cwd = path.join(process.resourcesPath, 'backend');
  } else {
    // In development mode
    pythonScript = path.join(__dirname, '..', 'backend', 'main.py');
    cwd = path.join(__dirname, '..', 'backend');
  }

  console.log(`Starting Python backend at: ${pythonScript} in directory: ${cwd}`);

  // Spawn the python process
  pythonProcess = spawn('python', [pythonScript], {
    cwd: cwd,
    shell: true, // Use shell to ensure python is found in PATH on Windows
    env: process.env
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python Backend]: ${data.toString().trim()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python Error]: ${data.toString().trim()}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`Python backend subprocess exited with code ${code}`);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 850,
    minWidth: 1200,
    minHeight: 750,
    backgroundColor: '#0a0b10', // Dark background to prevent flash when loading
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    },
    title: "PhonRec 语音学多发音人录音软件"
  });

  // Load front-end
  const isDev = !app.isPackaged;
  if (isDev) {
    // Dev server URL
    mainWindow.loadURL('http://localhost:5173');
    // Open DevTools in dev mode
    mainWindow.webContents.openDevTools();
  } else {
    // Prod built file path
    mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// App lifecycle
app.on('ready', () => {
  startPythonBackend();
  // Wait a moment for Python server to bind to port 8080 before creating window
  setTimeout(createWindow, 1000);
});

app.on('window-all-closed', () => {
  // Terminate python server process to prevent listener ports from leaking
  if (pythonProcess) {
    console.log('Terminating Python backend subprocess...');
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', pythonProcess.pid, '/f', '/t']);
      } else {
        pythonProcess.kill();
      }
    } catch (e) {
      console.error('Failed to kill python process:', e);
    }
  }

  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  if (pythonProcess) {
    console.log('Terminating Python backend subprocess (will-quit)...');
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', pythonProcess.pid, '/f', '/t']);
      } else {
        pythonProcess.kill();
      }
    } catch (e) {
      console.error(e);
    }
  }
});
