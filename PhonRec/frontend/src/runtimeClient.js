import { invoke } from '@tauri-apps/api/core';
import { apiFetch } from './engineApi.js';
import { parseWordlistFile } from './plainWordlist.js';

export const ENGINE_CAPABILITIES = Object.freeze({
  projectArchive: true,
  advancedWordlist: true,
  spectrogram: true,
  fullQuality: true,
  lightQuality: true,
  wavFolderExport: false,
});

export const STANDALONE_CAPABILITIES = Object.freeze({
  projectArchive: true,
  advancedWordlist: true,
  spectrogram: false,
  fullQuality: false,
  lightQuality: true,
  wavFolderExport: true,
});

const parseJsonResponse = async (response, fallbackMessage) => {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || fallbackMessage);
  }
  return response.json();
};

export function createEngineRuntimeClient() {
  return {
    mode: 'engine',
    capabilities: ENGINE_CAPABILITIES,
    async loadProject() {
      return parseJsonResponse(await apiFetch('/project/state'), '读取工程状态失败');
    },
    async saveProject(state) {
      return parseJsonResponse(await apiFetch('/project/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state),
      }), '保存工程状态失败');
    },
    async clearProject() {
      return parseJsonResponse(await apiFetch('/project/clear', { method: 'POST' }), '清空工作区失败');
    },
    async importWordlist(file) {
      const formData = new FormData();
      formData.append('file', file);
      return parseJsonResponse(await apiFetch('/wordlist/import', { method: 'POST', body: formData }), '解析字表失败');
    },
    async importProject(file) {
      const formData = new FormData();
      formData.append('file', file);
      return parseJsonResponse(await apiFetch('/project/import', { method: 'POST', body: formData }), '导入工程失败');
    },
    async exportProject() {
      const response = await apiFetch('/project/export');
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || '导出工程失败');
      }
      return response.blob();
    },
    async saveAudio({ blob, speakerId, wordId, source, qualityRules }) {
      const formData = new FormData();
      formData.append('file', blob, `${speakerId}_${wordId}.wav`);
      formData.append('speaker_id', speakerId);
      formData.append('word_id', wordId);
      formData.append('source', source);
      formData.append('quality_config', JSON.stringify(qualityRules));
      return parseJsonResponse(await apiFetch('/audio/save', { method: 'POST', body: formData }), '保存音频失败');
    },
    async readAudio({ speakerId, wordId }) {
      const response = await apiFetch(`/audio/file?speaker_id=${encodeURIComponent(speakerId)}&word_id=${encodeURIComponent(wordId)}&t=${Date.now()}`);
      if (!response.ok) throw new Error('读取录音失败');
      return response.blob();
    },
    async analyzeAudio({ speakerId, wordId, qualityRules }) {
      const formData = new FormData();
      formData.append('speaker_id', speakerId);
      formData.append('word_id', wordId);
      formData.append('quality_config', JSON.stringify(qualityRules));
      return parseJsonResponse(await apiFetch('/audio/analyze', { method: 'POST', body: formData }), '分析录音失败');
    },
    async exportWavFolder() {
      throw new Error('完整模式请使用工程导出');
    },
  };
}

export function createStandaloneRuntimeClient() {
  return {
    mode: 'standalone',
    capabilities: STANDALONE_CAPABILITIES,
    loadProject: () => invoke('standalone_project_load'),
    async saveProject(state) {
      const savedState = await invoke('standalone_project_save', { state });
      return { status: 'success', state: savedState };
    },
    async clearProject() {
      await invoke('standalone_project_clear');
      return { status: 'success' };
    },
    async importWordlist(file) {
      return { status: 'success', groups: await parseWordlistFile(file) };
    },
    async importProject(file) {
      const archiveBytes = Array.from(new Uint8Array(await file.arrayBuffer()));
      return invoke('standalone_project_import', { archiveBytes });
    },
    async exportProject() {
      const bytes = await invoke('standalone_project_export');
      return new Blob([new Uint8Array(bytes)], { type: 'application/zip' });
    },
    async saveAudio({ blob, speakerId, wordId, source, qualityRules }) {
      const wavBytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
      return invoke('standalone_audio_save', {
        wavBytes,
        speakerId,
        wordId,
        source,
        qualityRules,
      });
    },
    async readAudio({ speakerId, wordId }) {
      const bytes = await invoke('standalone_audio_read', { speakerId, wordId });
      return new Blob([new Uint8Array(bytes)], { type: 'audio/wav' });
    },
    async analyzeAudio() {
      return null;
    },
    exportWavFolder: (destination) => invoke('standalone_export_wav_folder', { destination }),
  };
}
