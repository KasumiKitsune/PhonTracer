import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.jsx';
import RuntimeProvider from './RuntimeProvider.jsx';
import { createEngineRuntimeClient } from './runtimeClient.js';

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  invoke: vi.fn(),
}));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => mocks.invoke(...args),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async () => () => {}),
}));
vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: vi.fn(),
  save: vi.fn(),
}));
vi.mock('@tauri-apps/plugin-fs', () => ({
  writeFile: vi.fn(),
}));
vi.mock('./engineApi.js', () => ({
  apiFetch: (...args) => mocks.apiFetch(...args),
}));

const savedSettings = {
  version: 1,
  realtime_quality: false,
  quality_rules: {},
  default_plot: 'waveform',
  record_order: 'wordlist',
  record_mode: 'click',
  record_source: 'default',
  sample_rate: 16000,
  channels: 1,
  format: 'wav',
  save_format: 'teproj',
  folder_path: '',
  wav_export_path: 'D:\\独立WAV',
  theme: 'light',
  accent_color: 'green',
  ui_scale: '100%',
  ui_density: 'standard',
  animations_enabled: true,
  primary_meta_key: '拼音',
  badge_meta_key: '拼音',
  char_font_size: 120,
  vad_preset: 'standard',
  shortcut_preset: 'standard',
  live_input_monitor: false,
  default_project_name: 'PhonRec_Project.teproj',
  show_shortcut_hints: true,
  skip_silence_on_play: false,
};

describe('清空工作区', () => {
  beforeEach(() => {
    mocks.invoke.mockImplementation(async (command) => {
      if (command === 'load_settings') return { ...savedSettings };
      if (command === 'list_audio_devices') return [];
      if (command === 'stop_loopback_listener') return null;
      if (command === 'reset_settings') {
        return { ...savedSettings, accent_color: 'blue' };
      }
      return null;
    });
    mocks.apiFetch.mockImplementation(async (path, options = {}) => {
      if (path === '/project/state' && !options.method) {
        return {
          ok: true,
          json: async () => ({ version: '1.0', speakers: {}, groups: [] }),
        };
      }
      if (path === '/project/clear' && options.method === 'POST') {
        return { ok: true, json: async () => ({ status: 'success' }) };
      }
      return { ok: true, json: async () => ({}) };
    });

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { enumerateDevices: vi.fn(async () => []) },
    });
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      fillRect: vi.fn(),
    });
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    mocks.apiFetch.mockReset();
    mocks.invoke.mockReset();
    document.documentElement.removeAttribute('data-accent');
  });

  it('保留已保存设置，并让面板重新采用这些设置', async () => {
    render(
      <RuntimeProvider client={createEngineRuntimeClient()}>
        <App />
      </RuntimeProvider>
    );

    await waitFor(() => {
      expect(document.documentElement.getAttribute('data-accent')).toBe('green');
    });

    fireEvent.click(screen.getByRole('button', { name: '清空工作区' }));
    fireEvent.click(await screen.findByRole('button', { name: '确定' }));

    expect(await screen.findByText('工作区已清空，原有设置保持不变')).toBeTruthy();
    expect(document.documentElement.getAttribute('data-accent')).toBe('green');
    expect(mocks.invoke).not.toHaveBeenCalledWith('reset_settings');
    expect(mocks.invoke.mock.calls.filter(([command]) => command === 'load_settings')).toHaveLength(2);
  });
});
