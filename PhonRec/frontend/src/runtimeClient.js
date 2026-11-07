import { invoke } from '@tauri-apps/api/core';
import { parseWordlistFile } from './plainWordlist.js';
import { renderSpectrogramDataUrl } from './spectrogramRenderer.js';

export const FULL_CAPABILITIES = Object.freeze({
  projectArchive: true,
  advancedWordlist: true,
  spectrogram: true,
  fullQuality: true,
  lightQuality: true,
  wavFolderExport: true,
});

export const ENGINE_CAPABILITIES = FULL_CAPABILITIES;
export const STANDALONE_CAPABILITIES = FULL_CAPABILITIES;

function createNativeRuntimeClient(mode = 'standalone') {
  return {
    mode,
    capabilities: FULL_CAPABILITIES,
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
    exportProjectFolder: (payload) =>
      invoke('standalone_export_folder', {
        payload: typeof payload === 'string' ? { folder_path: payload } : (payload || {}),
      }),
    importProjectFolder: (payload) =>
      invoke('standalone_import_folder', {
        payload: typeof payload === 'string' ? { folder_path: payload } : (payload || {}),
      }),
    async saveAudio({ blob, speakerId, wordId, source, qualityRules }) {
      const wavBytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
      const res = await invoke('standalone_audio_save', {
        wavBytes,
        speakerId,
        wordId,
        source,
        qualityRules,
      });
      const spectrogramUrl = await renderSpectrogramDataUrl(blob);
      return {
        ...res,
        spectrogram: spectrogramUrl,
        spectrogram_url: spectrogramUrl,
      };
    },
    async readAudio({ speakerId, wordId }) {
      const bytes = await invoke('standalone_audio_read', { speakerId, wordId });
      return new Blob([new Uint8Array(bytes)], { type: 'audio/wav' });
    },
    async analyzeAudio({ speakerId, wordId, qualityRules }) {
      let res = null;
      try {
        res = await invoke('standalone_audio_analyze', {
          speakerId,
          wordId,
          qualityRules,
        });
      } catch (err) {
        console.warn('音频质量分析跳过:', err);
      }
      let spectrogramUrl = '';
      try {
        const bytes = await invoke('standalone_audio_read', { speakerId, wordId });
        const blob = new Blob([new Uint8Array(bytes)], { type: 'audio/wav' });
        spectrogramUrl = await renderSpectrogramDataUrl(blob);
      } catch (err) {
        console.warn('语谱图生成跳过:', err);
      }
      return {
        status: 'success',
        quality: res?.quality,
        spectrogram: spectrogramUrl,
        spectrogram_url: spectrogramUrl,
      };
    },
    async openInPhonTracer({ speakerId, wordId } = {}) {
      return invoke('open_in_phontracer', {
        speakerId: speakerId || null,
        wordId: wordId || null,
      });
    },
    exportWavFolder: (destination) => invoke('standalone_export_wav_folder', { destination }),
  };
}

export function createEngineRuntimeClient() {
  return createNativeRuntimeClient('engine');
}

export function createStandaloneRuntimeClient() {
  return createNativeRuntimeClient('standalone');
}
