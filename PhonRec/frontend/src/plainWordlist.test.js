import { describe, expect, it } from 'vitest';
import {
  countPlainWordlist,
  normalizeAdvancedWordlist,
  parseCsvWordlist,
  parsePlainWordlist,
  PLAIN_WORDLIST_AI_PROMPT,
} from './plainWordlist.js';

describe('独立模式普通字表解析', () => {
  it('兼容分组标题、BOM 与常用分隔符，并保留斜杠音节边界', () => {
    let nextId = 0;
    const groups = parsePlainWordlist(
      '\ufeff【声调】\n妈 麻，马、骂\n[双音节]\n北/京\t上/海\n［英文］\nbro/ther, sis/ter\n#补充\n甲 乙',
      () => `id-${nextId++}`
    );

    expect(groups.map(group => group.name)).toEqual(['声调', '双音节', '英文', '补充']);
    expect(groups[0].items.map(item => item.label)).toEqual(['妈', '麻', '马', '骂']);
    expect(groups[1].items.map(item => item.label)).toEqual(['北/京', '上/海']);
    expect(countPlainWordlist(groups)).toBe(10);
    expect(groups[0].items[0].metadata_source).toBe('导入TXT');
  });

  it('没有显式标题时归入未分组，并忽略空白内容', () => {
    expect(parsePlainWordlist('   ')).toEqual([]);
    const groups = parsePlainWordlist('一，二 三', () => '固定标识');
    expect(groups).toHaveLength(1);
    expect(groups[0].name).toBe('未分组');
    expect(countPlainWordlist(groups)).toBe(3);
  });

  it('AI 提示词明确要求普通字表格式', () => {
    expect(PLAIN_WORDLIST_AI_PROMPT).toContain('每个组别名称用【】包裹');
    expect(PLAIN_WORDLIST_AI_PROMPT).toContain('[在此处粘贴你的字表]');
  });
});

describe('独立模式高级字表解析', () => {
  it('保留高级字段并修复跨分组重复 ID', () => {
    let nextId = 0;
    const groups = normalizeAdvancedWordlist({ groups: [
      { id: 'g', name: '甲组', note: '说明', tags: ['实验'], items: [{ id: 'same', label: '妈', meta: { 拼音: 'ma1' } }] },
      { id: 'g', name: '乙组', items: [{ id: 'same', label: '麻', tags: ['目标词'] }] },
    ] }, () => `new-${nextId++}`);

    expect(groups[0].note).toBe('说明');
    expect(groups[0].items[0].meta.拼音).toBe('ma1');
    expect(groups[1].id).not.toBe(groups[0].id);
    expect(groups[1].items[0].id).not.toBe(groups[0].items[0].id);
  });

  it('同时存在两套兼容字段时合并而不丢失信息', () => {
    const groups = normalizeAdvancedWordlist({ groups: [{
      name: '兼容组',
      tags: [],
      group_tags: ['组标签'],
      meta: { 实验条件: 'A' },
      group_meta: { 批次: '二' },
      items: [{
        label: '妈',
        tags: [],
        item_tags: ['目标词'],
        meta: { 声调: '阴平' },
        item_meta: { 拼音: 'mā' },
      }],
    }] });

    expect(groups[0].tags).toEqual(['组标签']);
    expect(groups[0].meta).toEqual({ 实验条件: 'A', 批次: '二' });
    expect(groups[0].items[0].tags).toEqual(['目标词']);
    expect(groups[0].items[0].meta).toEqual({ 声调: '阴平', 拼音: 'mā' });
  });

  it('CSV 支持引号、组元数据和自定义字段', () => {
    let nextId = 0;
    const groups = parseCsvWordlist(
      '组名,组备注,组标签,词项,词项备注,标签,别名,拼音\n"声调,组",说明,实验,妈,提示,目标词,ma1,mā',
      () => `csv-${nextId++}`
    );
    expect(groups[0].name).toBe('声调,组');
    expect(groups[0].note).toBe('说明');
    expect(groups[0].items[0].aliases).toEqual(['ma1']);
    expect(groups[0].items[0].meta.拼音).toBe('mā');
  });
});
