import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, setEngineConnection } from './engineApi.js';

describe('分析引擎 API 客户端', () => {
  afterEach(() => {
    setEngineConnection(null);
    vi.restoreAllMocks();
  });

  it('未连接分析引擎时拒绝请求', async () => {
    await expect(apiFetch('/project/state')).rejects.toThrow('尚未就绪');
  });

  it('自动附加动态地址与会话令牌', async () => {
    setEngineConnection({
      api_base: 'http://127.0.0.1:43123/api',
      token: 'session-token',
    });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
    await apiFetch('/project/state');
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:43123/api/project/state');
    expect(options.headers.get('Authorization')).toBe('Bearer session-token');
  });
});
