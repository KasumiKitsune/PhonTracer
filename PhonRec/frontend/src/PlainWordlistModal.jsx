import { useMemo, useRef, useState } from 'react';
import {
  countPlainWordlist,
  parsePlainWordlist,
  PLAIN_WORDLIST_AI_PROMPT,
} from './plainWordlist.js';

export default function PlainWordlistModal({ isOpen, initialText = '', initialTitle = '粘贴字表', onClose, onImport }) {
  const [text, setText] = useState(initialText);
  const [sourceTitle, setSourceTitle] = useState(initialTitle);
  const [message, setMessage] = useState('');
  const fileInputRef = useRef(null);
  const previewGroups = useMemo(() => parsePlainWordlist(text, () => 'preview'), [text]);
  const itemCount = countPlainWordlist(previewGroups);

  if (!isOpen) return null;

  const chooseFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.txt')) {
      setMessage('独立模式只能导入 TXT 普通字表。');
      return;
    }
    try {
      setText(await file.text());
      setSourceTitle(file.name);
      setMessage('');
    } catch (error) {
      setMessage(`读取 TXT 文件失败：${error}`);
    }
  };

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(PLAIN_WORDLIST_AI_PROMPT);
      setMessage('AI 字表整理提示词已复制。');
    } catch (error) {
      setMessage(`复制提示词失败：${error}`);
    }
  };

  const submit = async () => {
    const groups = parsePlainWordlist(text);
    const count = countPlainWordlist(groups);
    if (count === 0) {
      setMessage('没有识别到任何词项，请检查字表格式。');
      return;
    }
    setMessage('');
    await onImport({ groups, title: sourceTitle, count, rawText: text });
  };

  return (
    <div className="modal-overlay" style={{ zIndex: 9997 }} role="dialog" aria-modal="true" aria-label="导入普通字表">
      <div className="modal-content" style={{ width: 'min(620px, 92vw)', maxWidth: '620px' }}>
        <div className="modal-header">
          <strong>导入普通字表</strong>
        </div>
        <div className="modal-body" style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <button type="button" className="btn-primary" onClick={() => fileInputRef.current?.click()}>
              选择 TXT 文件
            </button>
            <button type="button" className="btn-secondary" onClick={copyPrompt}>
              复制 AI 整理提示词
            </button>
            <input ref={fileInputRef} type="file" accept=".txt" hidden onChange={chooseFile} />
          </div>
          <textarea
            className="form-input"
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setSourceTitle('粘贴字表');
              setMessage('');
            }}
            placeholder={'请粘贴或输入普通字表。\n\n【一组】\n妈 麻 马 骂\n#双音节\n北/京 bro/ther'}
            style={{ minHeight: '280px', resize: 'vertical', lineHeight: 1.6, fontFamily: 'var(--font-mono, monospace)' }}
          />
          <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
            已识别 {previewGroups.length} 个分组，共 {itemCount} 个词项。支持【】、[]、［］、# 分组标题，以及空格、Tab、逗号和顿号分隔。
          </div>
          {message && <div style={{ fontSize: '0.78rem', color: 'var(--color-accent)' }}>{message}</div>}
        </div>
        <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', padding: '0.75rem 1rem' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>取消</button>
          <button type="button" className="btn-primary" onClick={submit}>确认导入</button>
        </div>
      </div>
    </div>
  );
}
