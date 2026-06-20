import { describe, expect, it } from 'vitest';
import { countPlainWordlist, parsePlainWordlist, PLAIN_WORDLIST_AI_PROMPT } from './plainWordlist.js';

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
