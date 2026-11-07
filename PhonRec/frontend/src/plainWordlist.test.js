import { describe, expect, it } from 'vitest';
import {
  countPlainWordlist,
  normalizeAdvancedWordlist,
  parseCsvWordlist,
  parseJsonWordlist,
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

  it('JSON 支持 groups 结构、平铺数组与组名字典映射', () => {
    let nextId = 0;
    // 1. groups 结构
    const g1 = parseJsonWordlist(
      JSON.stringify({ groups: [{ name: '第一组', items: ['苹果', '香蕉'] }] }),
      () => `json-${nextId++}`
    );
    expect(g1[0].name).toBe('第一组');
    expect(g1[0].items.map(i => i.label)).toEqual(['苹果', '香蕉']);

    // 2. 平铺数组
    const g2 = parseJsonWordlist(
      JSON.stringify(['红', '黄', '蓝']),
      () => `json-${nextId++}`
    );
    expect(g2[0].name).toBe('默认组');
    expect(g2[0].items.map(i => i.label)).toEqual(['红', '黄', '蓝']);

    // 3. 字典分组映射
    const g3 = parseJsonWordlist(
      JSON.stringify({ "水果": ["苹果", "西瓜"], "蔬菜": ["白菜"] }),
      () => `json-${nextId++}`
    );
    expect(g3.map(g => g.name)).toEqual(['水果', '蔬菜']);
    expect(g3[0].items.map(i => i.label)).toEqual(['苹果', '西瓜']);
    expect(g3[1].items.map(i => i.label)).toEqual(['白菜']);

    // 4. parsePlainWordlist 自动检测并解析 JSON
    const g4 = parsePlainWordlist(
      JSON.stringify({ groups: [{ name: '自动JSON组', items: ['测试'] }] }),
      () => `json-${nextId++}`
    );
    expect(g4[0].name).toBe('自动JSON组');
    expect(g4[0].items[0].label).toBe('测试');
  });
});
