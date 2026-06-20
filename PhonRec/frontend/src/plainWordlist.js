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

const splitList = (value) => String(value || '')
  .split(/[;；,，]/u)
  .map(item => item.trim())
  .filter(Boolean);

const toList = (value) => Array.isArray(value)
  ? value.map(item => String(item).trim()).filter(Boolean)
  : splitList(value);

const mergeLists = (...values) => [...new Set(values.flatMap(toList))];

const toObject = (value) => value && typeof value === 'object' && !Array.isArray(value)
  ? value
  : {};

const parseCsvRows = (text) => {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  const source = String(text || '').replace(/^\ufeff/u, '');
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      if (char === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ',') {
      row.push(field);
      field = '';
    } else if (char === '\n') {
      row.push(field.replace(/\r$/u, ''));
      if (row.some(value => value.trim())) rows.push(row);
      row = [];
      field = '';
    } else {
      field += char;
    }
  }
  row.push(field.replace(/\r$/u, ''));
  if (row.some(value => value.trim())) rows.push(row);
  if (quoted) throw new Error('CSV 存在未闭合的引号');
  return rows;
};

const uniqueId = (candidate, used, idFactory) => {
  let value = String(candidate || '').trim();
  if (!value || used.has(value)) {
    do value = idFactory(); while (used.has(value));
  }
  used.add(value);
  return value;
};

export function normalizeAdvancedWordlist(raw, idFactory = defaultIdFactory) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.groups)) {
    throw new Error('高级字表缺少 groups 数组');
  }
  const usedGroupIds = new Set();
  const usedItemIds = new Set();
  const groups = raw.groups.map((group, groupIndex) => {
    if (!group || typeof group !== 'object') throw new Error(`第 ${groupIndex + 1} 个分组格式无效`);
    const items = Array.isArray(group.items) ? group.items : [];
    return {
      id: uniqueId(group.id, usedGroupIds, idFactory),
      name: String(group.name || group.group || `组${groupIndex + 1}`).trim() || `组${groupIndex + 1}`,
      note: String(group.note || group.group_note || ''),
      tags: mergeLists(group.tags, group.group_tags),
      meta: { ...toObject(group.meta), ...toObject(group.group_meta) },
      items: items.map((item, itemIndex) => {
        const source = typeof item === 'string' ? { label: item } : item;
        if (!source || typeof source !== 'object') throw new Error(`第 ${groupIndex + 1} 组第 ${itemIndex + 1} 条格式无效`);
        const label = String(source.label || source.word || '').trim();
        if (!label) throw new Error(`第 ${groupIndex + 1} 组第 ${itemIndex + 1} 条缺少词项`);
        return {
          id: uniqueId(source.id, usedItemIds, idFactory),
          label,
          note: String(source.note || source.item_note || ''),
          tags: mergeLists(source.tags, source.item_tags),
          aliases: mergeLists(source.aliases, source.item_aliases),
          meta: { ...toObject(source.meta), ...toObject(source.item_meta) },
          metadata_source: String(source.metadata_source || '导入字表'),
        };
      }),
    };
  }).filter(group => group.items.length > 0);
  if (groups.length === 0) throw new Error('字表中没有可录制词项');
  return groups;
}

export function parseCsvWordlist(rawText, idFactory = defaultIdFactory) {
  const rows = parseCsvRows(rawText);
  if (rows.length < 2) throw new Error('CSV 字表没有数据行');
  const headers = rows[0].map(value => value.trim());
  const indexOf = (...names) => headers.findIndex(header => names.includes(header));
  const groupIndex = indexOf('组名', '组别', 'group', 'group_name');
  const labelIndex = indexOf('词项', '字词', 'word', 'label', 'item');
  if (labelIndex < 0) throw new Error('CSV 字表缺少“词项”列');
  const reserved = new Set(['组名', '组别', 'group', 'group_name', '组备注', 'group_note', '组标签', 'group_tags', '词项', '字词', 'word', 'label', 'item', '词项备注', '备注', 'item_note', '标签', 'tags', '别名', 'aliases', '复核状态', 'metadata_source']);
  const grouped = new Map();
  for (const row of rows.slice(1)) {
    const label = String(row[labelIndex] || '').trim();
    if (!label) continue;
    const groupName = String(groupIndex >= 0 ? row[groupIndex] || '默认组' : '默认组').trim() || '默认组';
    if (!grouped.has(groupName)) grouped.set(groupName, { name: groupName, items: [] });
    const read = (...names) => {
      const index = indexOf(...names);
      return index >= 0 ? row[index] || '' : '';
    };
    const meta = {};
    headers.forEach((header, index) => {
      if (header && !reserved.has(header) && String(row[index] || '').trim()) meta[header] = String(row[index]).trim();
    });
    const group = grouped.get(groupName);
    group.note ||= String(read('组备注', 'group_note'));
    group.tags = mergeLists(group.tags, read('组标签', 'group_tags'));
    group.items.push({
      label,
      note: String(read('词项备注', '备注', 'item_note')),
      tags: splitList(read('标签', 'tags')),
      aliases: splitList(read('别名', 'aliases')),
      meta,
      metadata_source: String(read('复核状态', 'metadata_source') || '导入CSV'),
    });
  }
  return normalizeAdvancedWordlist({ groups: [...grouped.values()] }, idFactory);
}

export async function parseWordlistFile(file, idFactory = defaultIdFactory) {
  const name = String(file?.name || '').toLowerCase();
  const text = await file.text();
  if (name.endsWith('.ptwl')) {
    let raw;
    try {
      raw = JSON.parse(text.replace(/^\ufeff/u, ''));
    } catch (error) {
      throw new Error(`高级字表 JSON 无效：${error.message}`, { cause: error });
    }
    return normalizeAdvancedWordlist(raw, idFactory);
  }
  if (name.endsWith('.csv')) return parseCsvWordlist(text, idFactory);
  if (name.endsWith('.txt')) {
    const groups = parsePlainWordlist(text, idFactory);
    if (groups.length === 0) throw new Error('普通字表中没有可录制词项');
    return groups;
  }
  throw new Error('仅支持 .ptwl、.csv 或 .txt 字表');
}
