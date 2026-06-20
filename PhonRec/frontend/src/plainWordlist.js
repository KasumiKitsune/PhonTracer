export const PLAIN_WORDLIST_AI_PROMPT = `请帮我把下面这段字表转换成特定格式：
1. 每个组别名称用【】包裹并独占一行
2. 组别下的词或字跟在组别名称下面，可以一行一个，也可以用空格或逗号分隔
3. 去除所有不相关的序号、拼音和多余空行

示例输出格式：
【阴平】
八 扒 吧
【双音节】
音频 视频

以下是我的原始字表，请直接返回转换后的结果即可：

[在此处粘贴你的字表]`;

const defaultIdFactory = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().slice(0, 8);
  return Math.random().toString(36).slice(2, 10);
};

const groupNameFromHeader = (line) => line
  .replaceAll('【', '')
  .replaceAll('】', '')
  .replaceAll('[', '')
  .replaceAll(']', '')
  .replaceAll('［', '')
  .replaceAll('］', '')
  .replaceAll('#', '')
  .trim();

export function parsePlainWordlist(rawText, idFactory = defaultIdFactory) {
  const groups = [];
  let currentName = '未分组';
  let currentItems = [];

  const flushGroup = () => {
    if (currentItems.length === 0) return;
    groups.push({
      id: idFactory(),
      name: currentName || '未分组',
      note: '',
      tags: [],
      items: currentItems,
    });
    currentItems = [];
  };

  for (const sourceLine of String(rawText || '').replace(/^\ufeff/, '').split(/\r?\n/u)) {
    const line = sourceLine.trim();
    if (!line) continue;
    if (line.startsWith('【') || line.startsWith('[') || line.startsWith('［') || line.startsWith('#')) {
      flushGroup();
      currentName = groupNameFromHeader(line) || '未分组';
      continue;
    }
    const words = line.split(/[,\s\t，、]+/u).map(word => word.trim()).filter(Boolean);
    for (const word of words) {
      currentItems.push({
        id: idFactory(),
        label: word,
        note: '',
        tags: [],
        aliases: [],
        meta: {},
        metadata_source: '导入TXT',
      });
    }
  }
  flushGroup();
  return groups;
}

export function countPlainWordlist(groups) {
  return groups.reduce((total, group) => total + (group.items?.length || 0), 0);
}
