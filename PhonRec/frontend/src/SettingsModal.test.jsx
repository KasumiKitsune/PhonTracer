import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SettingsModal from './SettingsModal.jsx';

describe('SettingsModal 设置界面', () => {
  afterEach(() => {
    cleanup();
  });

  it('在"录音与导航"面板中渲染"操作提示显示"开关', async () => {
    const mockOnUpdate = vi.fn();
    const settings = {
      theme: 'light',
      ui_scale: '100%',
      ui_density: 'standard',
      animations_enabled: true,
      default_plot: 'waveform',
      record_order: 'wordlist',
      record_mode: 'click',
      record_source: 'default',
      sample_rate: 16000,
      save_format: 'teproj',
      folder_path: '',
      primary_meta_key: '拼音',
      badge_meta_key: '拼音',
      char_font_size: 120,
      vad_preset: 'standard',
      shortcut_preset: 'standard',
      live_input_monitor: true,
      default_project_name: 'PhonRec_Project.teproj',
      realtime_quality: true,
      quality_rules: {},
      show_shortcut_hints: true
    };

    render(
      <SettingsModal
        isOpen={true}
        settings={settings}
        onUpdate={mockOnUpdate}
        audioDevices={[]}
      />
    );

    // 默认是 "外观与显示" 标签页，切换到 "录音与导航"
    const tabBtn = screen.getByText('录音与导航');
    expect(tabBtn).toBeTruthy();
    fireEvent.click(tabBtn);

    // 应该能看到 "操作提示显示" 开关
    const label = screen.getByText('操作提示显示');
    expect(label).toBeTruthy();

    const switchText = screen.getByText('显示快捷键操作提示');
    expect(switchText).toBeTruthy();

    // 触发开关状态改变
    const checkbox = label.closest('.form-group').querySelector('input[type="checkbox"]');
    fireEvent.click(checkbox);

    expect(mockOnUpdate).toHaveBeenCalledWith({ show_shortcut_hints: false });
  });

  it('在"外观与显示"面板中渲染"颜色主题"下拉选择器', async () => {
    const mockOnUpdate = vi.fn();
    const settings = {
      theme: 'light',
      accent_color: 'blue',
      ui_scale: '100%',
      ui_density: 'standard',
      animations_enabled: true,
      default_plot: 'waveform',
      record_order: 'wordlist',
      record_mode: 'click',
      record_source: 'default',
      sample_rate: 16000,
      save_format: 'teproj',
      folder_path: '',
      primary_meta_key: '拼音',
      badge_meta_key: '拼音',
      char_font_size: 120,
      vad_preset: 'standard',
      shortcut_preset: 'standard',
      live_input_monitor: true,
      default_project_name: 'PhonRec_Project.teproj',
      realtime_quality: true,
      quality_rules: {},
      show_shortcut_hints: true
    };

    render(
      <SettingsModal
        isOpen={true}
        settings={settings}
        onUpdate={mockOnUpdate}
        audioDevices={[]}
      />
    );

    // 应该能看到 "颜色主题" 选择器
    const label = screen.getByText('颜色主题');
    expect(label).toBeTruthy();

    // 找到所有颜色圆点按钮
    const dots = label.closest('.form-group').querySelectorAll('.color-dot-btn');
    expect(dots.length).toBe(5);

    // settings.accent_color 传入 'blue'，所以对应的按钮（title="蓝色"）应该是 active 状态
    const blueDot = label.closest('.form-group').querySelector('button[title="蓝色"]');
    expect(blueDot.className).toContain('active');

    // 模拟点击深蓝色按钮
    const navyDot = label.closest('.form-group').querySelector('button[title="深蓝色"]');
    fireEvent.click(navyDot);

    expect(mockOnUpdate).toHaveBeenCalledWith({ accent_color: 'navy' });
  });

  it('独立模式仅临时使用波形并隐藏工程格式设置', () => {
    const settings = {
      theme: 'light', accent_color: 'blue', ui_scale: '100%', ui_density: 'standard',
      animations_enabled: true, default_plot: 'spectrogram', record_order: 'wordlist',
      record_mode: 'click', record_source: 'default', sample_rate: 16000,
      save_format: 'teproj', folder_path: 'D:\\WAV', primary_meta_key: '拼音', badge_meta_key: '拼音',
      char_font_size: 120, vad_preset: 'standard', shortcut_preset: 'standard',
      live_input_monitor: true, default_project_name: 'PhonRec_Project.teproj',
      realtime_quality: true, quality_rules: {}, show_shortcut_hints: true,
    };
    const onUpdate = vi.fn();
    render(
      <SettingsModal
        isOpen
        settings={settings}
        onUpdate={onUpdate}
        audioDevices={[]}
        runtimeMode="standalone"
        capabilities={{ spectrogram: false, fullQuality: false }}
      />
    );

    expect(screen.getByText('波形图（独立模式）')).toBeTruthy();
    expect(screen.getByText(/不会修改您保存的默认图形/)).toBeTruthy();
    expect(onUpdate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('保存与导出'));
    expect(screen.getByText('默认 WAV 导出目录')).toBeTruthy();
    expect(screen.queryByText('默认工程保存格式')).toBeNull();
    expect(screen.queryByText('默认工程文件名')).toBeNull();
  });

  it('独立模式只允许调整音量和削波规则', () => {
    const rules = Object.fromEntries(
      ['speech', 'volume', 'clipping', 'noise', 'creak', 'dc_offset'].map(key => [key, { enabled: true, level: 'medium' }])
    );
    const settings = {
      theme: 'light', accent_color: 'blue', ui_scale: '100%', ui_density: 'standard',
      animations_enabled: true, default_plot: 'waveform', record_order: 'wordlist',
      record_mode: 'click', record_source: 'default', sample_rate: 16000,
      save_format: 'teproj', folder_path: '', primary_meta_key: '拼音', badge_meta_key: '拼音',
      char_font_size: 120, vad_preset: 'standard', shortcut_preset: 'standard',
      live_input_monitor: true, default_project_name: 'PhonRec_Project.teproj',
      realtime_quality: true, quality_rules: rules, show_shortcut_hints: true,
    };
    const onUpdate = vi.fn();
    render(
      <SettingsModal
        isOpen
        settings={settings}
        onUpdate={onUpdate}
        audioDevices={[]}
        runtimeMode="standalone"
        capabilities={{ spectrogram: false, fullQuality: false }}
      />
    );

    fireEvent.click(screen.getByText('质量检测'));
    expect(screen.getByText('已启用 2 / 2 项可用检测')).toBeTruthy();
    expect(screen.getAllByText('完整模式可用')).toHaveLength(4);
    const summarySwitch = screen.getByText('录音质量判定条件').closest('.quality-settings-summary').querySelector('input');
    fireEvent.click(summarySwitch);

    const update = onUpdate.mock.calls[0][0];
    expect(update.quality_rules.volume.enabled).toBe(false);
    expect(update.quality_rules.clipping.enabled).toBe(false);
    expect(update.quality_rules.speech.enabled).toBe(true);
    expect(update.quality_rules.noise.enabled).toBe(true);
  });
});
