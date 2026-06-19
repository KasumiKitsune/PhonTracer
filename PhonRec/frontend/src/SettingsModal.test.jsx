import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import React from 'react';
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
});
