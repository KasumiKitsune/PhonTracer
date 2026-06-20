import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.jsx';
import RuntimeProvider from './RuntimeProvider.jsx';
import { STANDALONE_CAPABILITIES } from './runtimeClient.js';

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock('@tauri-apps/api/core', () => ({ invoke: (...args) => mocks.invoke(...args) }));
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }));
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn(), save: vi.fn() }));
vi.mock('@tauri-apps/plugin-fs', () => ({ writeFile: vi.fn() }));

const settings = {
  version: 1,
  realtime_quality: true,
  quality_rules: {
    speech: { enabled: true, level: 'medium' },
    volume: { enabled: true, level: 'medium' },
    clipping: { enabled: true, level: 'medium' },
    noise: { enabled: true, level: 'medium' },
    creak: { enabled: true, level: 'medium' },
    dc_offset: { enabled: true, level: 'medium' },
  },
  default_plot: 'spectrogram',
  record_order: 'wordlist', record_mode: 'click', record_source: 'default', sample_rate: 16000,
  save_format: 'teproj', folder_path: '', theme: 'light', accent_color: 'blue', ui_scale: '100%',
  ui_density: 'standard', animations_enabled: true, primary_meta_key: '拼音', badge_meta_key: '拼音',
  char_font_size: 120, vad_preset: 'standard', shortcut_preset: 'standard', live_input_monitor: false,
  default_project_name: 'PhonRec_Project.teproj', show_shortcut_hints: true,
};

const client = {
  mode: 'standalone',
  capabilities: STANDALONE_CAPABILITIES,
  loadProject: vi.fn(async () => ({ version: '1.0', speakers: {}, groups: [] })),
  saveProject: vi.fn(async state => ({ status: 'success', state })),
  clearProject: vi.fn(async () => ({ status: 'success' })),
  saveAudio: vi.fn(),
  readAudio: vi.fn(),
  analyzeAudio: vi.fn(),
  exportWavFolder: vi.fn(),
};

describe('独立软件模式主界面', () => {
  beforeEach(() => {
    mocks.invoke.mockImplementation(async command => {
      if (command === 'load_settings') return { ...settings };
      if (command === 'list_audio_devices') return [];
      return null;
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { enumerateDevices: vi.fn(async () => []) },
    });
    window.matchMedia = vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn() });
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    mocks.invoke.mockReset();
    Object.values(client).forEach(value => value?.mockClear?.());
  });

  it('隐藏工程能力，强制波形并通过普通字表弹窗导入', async () => {
    const { container } = render(
      <RuntimeProvider client={client}>
        <App />
      </RuntimeProvider>
    );

    expect(await screen.findByText(/独立录音模式/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /导出 WAV/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '导入' })).toBeNull();
    expect(container.querySelector('input[accept=".teproj"]')).toBeNull();
    expect(container.querySelector('input[accept=".ptwl,.txt,.csv"]')).toBeNull();
    expect(screen.queryByRole('button', { name: /语谱图/ })).toBeNull();
    expect(screen.queryAllByText('完整模式可用')).toHaveLength(0);
    expect(screen.queryByText('字段显示设置')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /导入字表/ }));
    expect(await screen.findByRole('dialog', { name: '导入普通字表' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '选择 TXT 文件' })).toBeTruthy();
    await waitFor(() => expect(client.loadProject).toHaveBeenCalled());
  });

  it('能够关闭独立录音模式提示横幅', async () => {
    localStorage.clear();
    const { queryByText } = render(
      <RuntimeProvider client={client}>
        <App />
      </RuntimeProvider>
    );

    expect(await screen.findByText(/独立录音模式/)).toBeTruthy();
    const closeBtn = screen.getByRole('button', { name: '关闭提示' });
    expect(closeBtn).toBeTruthy();

    fireEvent.click(closeBtn);
    expect(queryByText(/独立录音模式/)).toBeNull();
    expect(localStorage.getItem('hideStandaloneBanner')).toBe('true');
  });
});
