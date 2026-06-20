import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createEngineRuntimeClient,
  createStandaloneRuntimeClient,
  ENGINE_CAPABILITIES,
  STANDALONE_CAPABILITIES,
} from './runtimeClient.js';

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), apiFetch: vi.fn() }));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => mocks.invoke(...args),
}));
vi.mock('./engineApi.js', () => ({
  apiFetch: (...args) => mocks.apiFetch(...args),
}));

describe('独立模式运行时客户端', () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.apiFetch.mockReset();
  });

  it('公开完整字表与工程能力并封装工程读写和 WAV 导出', async () => {
    mocks.invoke.mockResolvedValue({ version: '1.0', speakers: {}, groups: [] });
    const client = createStandaloneRuntimeClient();

    expect(client.capabilities).toEqual(STANDALONE_CAPABILITIES);
    expect(client.capabilities.projectArchive).toBe(true);
    expect(client.capabilities.advancedWordlist).toBe(true);
    expect(client.capabilities.lightQuality).toBe(true);
    await client.loadProject();
    await client.saveProject({ version: '1.0' });
    await client.clearProject();
    await client.exportWavFolder('D:\\录音导出');

    expect(mocks.invoke).toHaveBeenNthCalledWith(1, 'standalone_project_load');
    expect(mocks.invoke).toHaveBeenNthCalledWith(2, 'standalone_project_save', { state: { version: '1.0' } });
    expect(mocks.invoke).toHaveBeenNthCalledWith(3, 'standalone_project_clear');
    expect(mocks.invoke).toHaveBeenNthCalledWith(4, 'standalone_export_wav_folder', { destination: 'D:\\录音导出' });
  });

  it('独立模式可导入高级字表并读写 teproj', async () => {
    mocks.invoke
      .mockResolvedValueOnce({ status: 'success', state: { version: '1.0' } })
      .mockResolvedValueOnce([80, 75, 3, 4]);
    const client = createStandaloneRuntimeClient();
    const wordlist = {
      name: '高级.ptwl',
      text: vi.fn(async () => JSON.stringify({ groups: [{ name: '实验组', items: [{ id: 'x', label: '妈', meta: { 拼音: 'ma1' } }] }] })),
    };
    const project = {
      arrayBuffer: vi.fn(async () => Uint8Array.from([80, 75, 3, 4]).buffer),
    };

    const importedWordlist = await client.importWordlist(wordlist);
    await client.importProject(project);
    const archive = await client.exportProject();

    expect(importedWordlist.groups[0].items[0].meta.拼音).toBe('ma1');
    expect(mocks.invoke).toHaveBeenNthCalledWith(1, 'standalone_project_import', { archiveBytes: [80, 75, 3, 4] });
    expect(mocks.invoke).toHaveBeenNthCalledWith(2, 'standalone_project_export');
    expect(archive.size).toBe(4);
  });

  it('录音保存和读取都转换二进制数据', async () => {
    mocks.invoke
      .mockResolvedValueOnce({ status: 'success' })
      .mockResolvedValueOnce([82, 73, 70, 70]);
    const client = createStandaloneRuntimeClient();
    const blob = { arrayBuffer: vi.fn(async () => Uint8Array.from([1, 2, 3]).buffer) };

    await client.saveAudio({
      blob,
      speakerId: '发音人',
      wordId: '词项',
      source: '麦克风',
      qualityRules: { volume: { enabled: true, level: 'medium' } },
    });
    const result = await client.readAudio({ speakerId: '发音人', wordId: '词项' });

    expect(mocks.invoke.mock.calls[0][0]).toBe('standalone_audio_save');
    expect(mocks.invoke.mock.calls[0][1].wavBytes).toEqual([1, 2, 3]);
    expect(mocks.invoke).toHaveBeenLastCalledWith('standalone_audio_read', { speakerId: '发音人', wordId: '词项' });
    expect(result.type).toBe('audio/wav');
    expect(result.size).toBe(4);
  });
});

describe('完整模式运行时客户端', () => {
  beforeEach(() => mocks.apiFetch.mockReset());

  it('保留工程归档、高级字表和完整分析能力及原有接口', async () => {
    mocks.apiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ version: '1.0', speakers: {}, groups: [] }),
    });
    const client = createEngineRuntimeClient();

    expect(client.capabilities).toEqual(ENGINE_CAPABILITIES);
    expect(client.capabilities.projectArchive).toBe(true);
    expect(client.capabilities.advancedWordlist).toBe(true);
    expect(client.capabilities.fullQuality).toBe(true);
    await client.loadProject();
    await client.saveProject({ version: '1.0' });
    await client.clearProject();

    expect(mocks.apiFetch).toHaveBeenNthCalledWith(1, '/project/state');
    expect(mocks.apiFetch.mock.calls[1][0]).toBe('/project/state');
    expect(mocks.apiFetch.mock.calls[1][1].method).toBe('POST');
    expect(mocks.apiFetch).toHaveBeenNthCalledWith(3, '/project/clear', { method: 'POST' });
  });
});
