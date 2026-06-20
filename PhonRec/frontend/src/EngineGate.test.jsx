import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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

  it.each([
    ['missing', '需要安装 PhonTracer'],
    ['incompatible', '需要更新 PhonTracer'],
    ['failed', '分析引擎启动失败'],
  ])('%s 状态允许进入独立软件模式', async (state, title) => {
    invokeMock.mockResolvedValue({
      state,
      message: '分析引擎当前不可用',
      connection: null,
      download_url: 'https://example.invalid',
    });
    render(<EngineGate><div>主界面</div></EngineGate>);
    expect(await screen.findByText(title)).toBeTruthy();
    expect(screen.queryByText('主界面')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '进入独立软件模式' }));
    expect(await screen.findByText('主界面')).toBeTruthy();
  });

  it('启动检测期间不允许进入独立模式', () => {
    invokeMock.mockReturnValue(new Promise(() => {}));
    render(<EngineGate><div>主界面</div></EngineGate>);
    expect(screen.getByText('正在连接 PhonTracer')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '进入独立软件模式' })).toBeNull();
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

  it('独立模式选择只在本次挂载有效', async () => {
    invokeMock.mockResolvedValue({
      state: 'missing',
      message: '尚未检测到已安装的 PhonTracer',
      connection: null,
      download_url: 'https://example.invalid',
    });
    const first = render(<EngineGate><div>主界面</div></EngineGate>);
    fireEvent.click(await screen.findByRole('button', { name: '进入独立软件模式' }));
    expect(screen.getByText('主界面')).toBeTruthy();
    first.unmount();

    render(<EngineGate><div>主界面</div></EngineGate>);
    expect(await screen.findByText('需要安装 PhonTracer')).toBeTruthy();
    expect(screen.queryByText('主界面')).toBeNull();
  });
});
