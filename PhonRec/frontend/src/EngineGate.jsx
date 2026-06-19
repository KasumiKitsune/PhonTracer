import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { openUrl } from '@tauri-apps/plugin-opener';
import { setEngineConnection } from './engineApi.js';

const STATE_TITLES = {
  starting: '正在连接 PhonTracer',
  missing: '需要安装 PhonTracer',
  incompatible: '需要更新 PhonTracer',
  failed: '分析引擎启动失败',
};

export default function EngineGate({ children }) {
  const [status, setStatus] = useState({
    state: 'starting',
    message: '正在检测 PhonTracer 分析引擎……',
    connection: null,
    download_url: 'https://github.com/KasumiKitsune/PhonTracer/releases/latest',
  });
  const [retrying, setRetrying] = useState(false);

  const applyStatus = useCallback((nextStatus) => {
    setEngineConnection(nextStatus?.state === 'ready' ? nextStatus.connection : null);
    setStatus(nextStatus);
  }, []);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const nextStatus = await invoke('get_engine_status');
        if (active) applyStatus(nextStatus);
      } catch (error) {
        if (active) {
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
    const timer = window.setInterval(refresh, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [applyStatus, status.download_url]);

  const retry = async () => {
    setRetrying(true);
    try {
      applyStatus(await invoke('retry_engine'));
    } catch (error) {
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

  if (status.state === 'ready') {
    return children;
  }

  return (
    <main className="engine-gate" role="alert">
      <section className="engine-gate-card">
        <div className="engine-gate-mark">PR</div>
        <p className="engine-gate-kicker">PhonRec 配套录制工具</p>
        <h1>{STATE_TITLES[status.state] || '暂时无法启动 PhonRec'}</h1>
        <p className="engine-gate-message">{status.message}</p>
        <p className="engine-gate-hint">PhonTracer 只需完成安装，不必保持主程序窗口运行。</p>
        <div className="engine-gate-actions">
          <button type="button" className="btn-primary" onClick={retry} disabled={retrying}>
            {retrying ? '正在检测……' : '重新检测'}
          </button>
          <button type="button" className="btn-secondary" onClick={() => openUrl(status.download_url)}>
            打开 PhonTracer 下载页
          </button>
          <button type="button" className="btn-quiet" onClick={() => invoke('quit_app')}>
            退出
          </button>
        </div>
      </section>
    </main>
  );
}
