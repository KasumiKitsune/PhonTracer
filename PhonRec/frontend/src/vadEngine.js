const DEFAULTS = {
  minNoiseFloor: 0.0015,
  enterRatio: 3.2,
  exitRatio: 1.8,
  minSpeechMs: 220,
  trailingSilenceMs: 700,
  noSpeechTimeoutMs: 8000,
  maxRecordingMs: 15000,
};

export function createVadEngine(options = {}) {
  const config = { ...DEFAULTS, ...options };
  let noiseFloor = config.minNoiseFloor;
  let startedAt = 0;
  let aboveSince = null;
  let belowSince = null;
  let speechStartedAt = null;
  let state = 'idle';

  const reset = (now = performance.now()) => {
    startedAt = now;
    aboveSince = null;
    belowSince = null;
    speechStartedAt = null;
    state = 'listening';
  };

  const observeNoise = (rms) => {
    if (!Number.isFinite(rms) || rms < 0) return;
    if (rms > Math.max(noiseFloor * 2.5, 0.012)) return;
    noiseFloor = noiseFloor * 0.96 + rms * 0.04;
  };

  const process = (rms, now = performance.now()) => {
    if (state === 'idle') reset(now);
    const enterThreshold = Math.max(config.minNoiseFloor * 2.2, noiseFloor * config.enterRatio);
    const exitThreshold = Math.max(config.minNoiseFloor * 1.4, noiseFloor * config.exitRatio);
    let event = null;

    if (speechStartedAt === null) {
      if (rms >= enterThreshold) {
        aboveSince ??= now;
        if (now - aboveSince >= config.minSpeechMs) {
          speechStartedAt = aboveSince;
          belowSince = null;
          state = 'speaking';
          event = 'speech-start';
        }
      } else {
        aboveSince = null;
        observeNoise(rms);
      }
      if (speechStartedAt === null && now - startedAt >= config.noSpeechTimeoutMs) {
        state = 'timeout';
        event = 'no-speech-timeout';
      }
    } else if (rms <= exitThreshold) {
      belowSince ??= now;
      state = 'trailing';
      if (now - belowSince >= config.trailingSilenceMs) {
        state = 'complete';
        event = 'speech-end';
      }
    } else {
      belowSince = null;
      state = 'speaking';
    }

    if (!event && now - startedAt >= config.maxRecordingMs) {
      state = 'complete';
      event = speechStartedAt === null ? 'no-speech-timeout' : 'max-duration';
    }

    return { event, state, noiseFloor, enterThreshold, exitThreshold, speaking: speechStartedAt !== null && state !== 'complete' };
  };

  return { reset, process, observeNoise, getState: () => state };
}
