import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EngineGate from './EngineGate.jsx';

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args) => invokeMock(...args) }));
vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: vi.fn() }));

describe('PhonTracer 启动门禁', () => {
  afterEach(() => {
    cleanup();
    invokeMock.mockReset();
  });

  it('缺少主程序时显示安装提示', async () => {
    invokeMock.mockResolvedValue({
      state: 'missing',
      message: '尚未检测到已安装的 PhonTracer',
      connection: null,
      download_url: 'https://example.invalid',
    });
    render(<EngineGate><div>主界面</div></EngineGate>);
    expect(await screen.findByText('需要安装 PhonTracer')).toBeTruthy();
    expect(screen.queryByText('主界面')).toBeNull();
  });

  it('分析引擎就绪后呈现主界面', async () => {
    invokeMock.mockResolvedValue({
      state: 'ready',
      message: '分析引擎已就绪',
      connection: {
        api_base: 'http://127.0.0.1:43123/api',
        token: 'token',
      },
      download_url: 'https://example.invalid',
    });
    render(<EngineGate><div>主界面</div></EngineGate>);
    expect(await screen.findByText('主界面')).toBeTruthy();
  });
});
