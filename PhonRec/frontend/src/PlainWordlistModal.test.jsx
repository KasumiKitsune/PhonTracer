import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PlainWordlistModal from './PlainWordlistModal.jsx';
import { PLAIN_WORDLIST_AI_PROMPT } from './plainWordlist.js';

describe('独立模式字表弹窗', () => {
  const onClose = vi.fn();
  const onImport = vi.fn(async () => {});
  const writeText = vi.fn(async () => {});

  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
  });

  afterEach(() => {
    cleanup();
    onClose.mockReset();
    onImport.mockReset();
    writeText.mockReset();
  });

  it('支持粘贴编辑、实时统计与确认导入', async () => {
    render(<PlainWordlistModal isOpen onClose={onClose} onImport={onImport} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '【甲组】\n一 二\n#乙组\n三' } });

    expect(screen.getByText(/已识别 2 个分组，共 3 个词项/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确认导入' }));

    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(1));
    expect(onImport.mock.calls[0][0].count).toBe(3);
    expect(onImport.mock.calls[0][0].groups.map(group => group.name)).toEqual(['甲组', '乙组']);
  });

  it('复制普通字表 AI 整理提示词', async () => {
    render(<PlainWordlistModal isOpen onClose={onClose} onImport={onImport} />);
    fireEvent.click(screen.getByRole('button', { name: '复制 AI 整理提示词' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PLAIN_WORDLIST_AI_PROMPT));
    expect(screen.getByText('AI 字表整理提示词已复制。')).toBeTruthy();
  });

  it('文件入口严格声明只接受 TXT', () => {
    const { container } = render(<PlainWordlistModal isOpen onClose={onClose} onImport={onImport} />);
    expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBe('.txt');
  });
});
