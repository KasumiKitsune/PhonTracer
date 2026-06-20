import { useCallback, useEffect, useMemo, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { openUrl } from '@tauri-apps/plugin-opener';
import { setEngineConnection } from './engineApi.js';
import {
  createEngineRuntimeClient,
  createStandaloneRuntimeClient,
} from './runtimeClient.js';
import RuntimeProvider from './RuntimeProvider.jsx';

const STATE_TITLES = {
  starting: '正在连接 PhonTracer',
  missing: '需要安装 PhonTracer',
  incompatible: '需要更新 PhonTracer',
  failed: '分析引擎启动失败',
};

function hideStartupLoader() {
  document.getElementById('startup-loader')?.remove();
}

export default function EngineGate({ children }) {
  const [status, setStatus] = useState({
    state: 'starting',
    message: '正在检测 PhonTracer 分析引擎……',
    connection: null,
    download_url: 'https://github.com/KasumiKitsune/PhonTracer/releases/latest',
  });
  const [retrying, setRetrying] = useState(false);
  const [standalone, setStandalone] = useState(false);
  const engineClient = useMemo(() => createEngineRuntimeClient(), []);
  const standaloneClient = useMemo(() => createStandaloneRuntimeClient(), []);

  const applyStatus = useCallback((nextStatus) => {
    setEngineConnection(nextStatus?.state === 'ready' ? nextStatus.connection : null);
    setStatus(nextStatus);
    if (nextStatus?.state !== 'starting') hideStartupLoader();
  }, []);

  useEffect(() => {
    if (standalone) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const nextStatus = await invoke('get_engine_status');
        if (active) applyStatus(nextStatus);
      } catch (error) {
        if (active) {
          hideStartupLoader();
          applyStatus({
            state: 'failed',
            message: `无法读取分析引擎状态：${error}`,
            connection: null,
            download_url: status.download_url,
          });
        }
      }
    };
    refresh();
    const timer = window.setInterval(refresh, status.state === 'starting' ? 250 : 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [applyStatus, standalone, status.download_url, status.state]);

  const retry = async () => {
    setRetrying(true);
    const startTime = Date.now();
    try {
      const result = await invoke('retry_engine');
      const elapsed = Date.now() - startTime;
      if (elapsed < 800) {
        await new Promise(resolve => window.setTimeout(resolve, 800 - elapsed));
      }
      applyStatus(result);
    } catch (error) {
      const elapsed = Date.now() - startTime;
      if (elapsed < 800) {
        await new Promise(resolve => window.setTimeout(resolve, 800 - elapsed));
      }
      applyStatus({
        ...status,
        state: 'failed',
        message: `重新检测失败：${error}`,
        connection: null,
      });
    } finally {
      setRetrying(false);
    }
  };

  if (standalone) {
    return (
      <RuntimeProvider client={standaloneClient}>
        {children}
      </RuntimeProvider>
    );
  }

  if (status.state === 'ready') {
    return (
      <RuntimeProvider client={engineClient}>
        {children}
      </RuntimeProvider>
    );
  }

  return (
    <main className="engine-gate" role="alert">
      <div className="engine-gate-content">
        {/* Header Block: Title & Warning Badge */}
        <div className="engine-gate-header-block">
          <div className="warning-badge">!</div>
          <h1>{STATE_TITLES[status.state] || '暂时无法启动 PhonRec'}</h1>
        </div>

        <p className="engine-gate-desc">
          {status.state === 'missing'
            ? '检测到 PhonTracer 未安装或关键组件缺失，请完成安装以继续使用录制功能。'
            : status.message}
        </p>

        {/* Options List */}
        <div className="engine-gate-options">
          {/* Option 1: 重新检测 */}
          <button
            type="button"
            className="engine-option-card option-primary"
            onClick={retry}
            disabled={retrying}
            aria-label={retrying ? '正在检测' : '重新检测'}
          >
            <div className="option-icon-wrapper">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </div>
            <div className="option-text-group">
              <span className="option-title">{retrying ? '正在检测……' : '重新检测'}</span>
              <span className="option-subtitle">检查并更新系统环境</span>
            </div>
            <div className="option-chevron">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </div>
          </button>

          {/* Option 2: 打开 PhonTracer 下载页 */}
          <button
            type="button"
            className="engine-option-card option-secondary"
            onClick={() => openUrl(status.download_url)}
            aria-label="打开 PhonTracer 下载页"
          >
            <div className="option-icon-wrapper">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </div>
            <div className="option-text-group">
              <span className="option-title">打开 PhonTracer 下载页</span>
              <span className="option-subtitle">前往官网下载最新版本</span>
            </div>
            <div className="option-chevron">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </div>
          </button>

          {/* Option 3: 进入独立软件模式 */}
          {['missing', 'incompatible', 'failed'].includes(status.state) && (
            <button
              type="button"
              className="engine-option-card option-secondary"
              onClick={() => {
                setEngineConnection(null);
                setStandalone(true);
              }}
              aria-label="进入独立软件模式"
            >
              <div className="option-icon-wrapper">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                  <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                  <line x1="12" y1="22.08" x2="12" y2="12" />
                </svg>
              </div>
              <div className="option-text-group">
                <span className="option-title">进入独立软件模式</span>
                <span className="option-subtitle">不安装，直接使用独立模式</span>
              </div>
              <div className="option-chevron">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </div>
            </button>
          )}
        </div>

        {/* Footer Exit Button */}
        <button
          type="button"
          className="engine-gate-exit-btn"
          onClick={() => invoke('quit_app')}
        >
          退出程序
        </button>
      </div>
    </main>
  );
}
