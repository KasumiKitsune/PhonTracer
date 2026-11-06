/**
 * Client-Side WebAudio / Canvas Spectrogram Renderer
 * Decodes standard PCM/Float WAV audio, computes FFT spectrogram, and renders high-contrast PNG DataURL.
 */

// Bit-reversal table precomputation for radix-2 FFT (N = 512)
const FFT_SIZE = 512;
const BIT_REV = new Uint16Array(FFT_SIZE);
for (let i = 0; i < FFT_SIZE; i++) {
  let rev = 0;
  let temp = i;
  for (let j = 0; j < 9; j++) {
    rev = (rev << 1) | (temp & 1);
    temp >>= 1;
  }
  BIT_REV[i] = rev;
}

// Precompute Hann window and twiddle factors
const HANN_WINDOW = new Float32Array(FFT_SIZE);
for (let i = 0; i < FFT_SIZE; i++) {
  HANN_WINDOW[i] = 0.5 * (1.0 - Math.cos((2.0 * Math.PI * i) / (FFT_SIZE - 1)));
}

const TWIDDLE_COS = new Float32Array(FFT_SIZE / 2);
const TWIDDLE_SIN = new Float32Array(FFT_SIZE / 2);
for (let i = 0; i < FFT_SIZE / 2; i++) {
  TWIDDLE_COS[i] = Math.cos((-2.0 * Math.PI * i) / FFT_SIZE);
  TWIDDLE_SIN[i] = Math.sin((-2.0 * Math.PI * i) / FFT_SIZE);
}

/**
 * Radix-2 in-place Cooley-Tukey FFT
 */
function radix2Fft(real, imag) {
  // Bit-reversal permutation
  for (let i = 0; i < FFT_SIZE; i++) {
    const j = BIT_REV[i];
    if (i < j) {
      const tempR = real[i];
      real[i] = real[j];
      real[j] = tempR;
      const tempI = imag[i];
      imag[i] = imag[j];
      imag[j] = tempI;
    }
  }

  // Cooley-Tukey butterflies
  for (let len = 2; len <= FFT_SIZE; len <<= 1) {
    const halfLen = len >> 1;
    const step = FFT_SIZE / len;
    for (let i = 0; i < FFT_SIZE; i += len) {
      for (let j = 0; j < halfLen; j++) {
        const k = j * step;
        const cos = TWIDDLE_COS[k];
        const sin = TWIDDLE_SIN[k];
        const r1 = real[i + j + halfLen];
        const i1 = imag[i + j + halfLen];
        const tR = r1 * cos - i1 * sin;
        const tI = r1 * sin + i1 * cos;
        real[i + j + halfLen] = real[i + j] - tR;
        imag[i + j + halfLen] = imag[i + j] - tI;
        real[i + j] += tR;
        imag[i + j] += tI;
      }
    }
  }
}

/**
 * Parses PCM/Float WAV header directly in JS
 */
function parseWavHeader(arrayBuffer) {
  if (!arrayBuffer || arrayBuffer.byteLength < 44) return null;
  const view = new DataView(arrayBuffer);

  const riff = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  const wave = String.fromCharCode(view.getUint8(8), view.getUint8(9), view.getUint8(10), view.getUint8(11));
  if (riff !== 'RIFF' || wave !== 'WAVE') return null;

  let offset = 12;
  let audioFormat = 1;
  let channels = 1;
  let sampleRate = 16000;
  let bitsPerSample = 16;
  let dataOffset = 0;
  let dataLength = 0;

  while (offset + 8 <= arrayBuffer.byteLength) {
    const chunkId = String.fromCharCode(
      view.getUint8(offset),
      view.getUint8(offset + 1),
      view.getUint8(offset + 2),
      view.getUint8(offset + 3)
    );
    const chunkSize = view.getUint32(offset + 4, true);
    const chunkStart = offset + 8;
    if (chunkId === 'fmt ' && chunkSize >= 16) {
      audioFormat = view.getUint16(chunkStart, true);
      channels = view.getUint16(chunkStart + 2, true);
      sampleRate = view.getUint32(chunkStart + 4, true);
      bitsPerSample = view.getUint16(chunkStart + 14, true);
    } else if (chunkId === 'data') {
      dataOffset = chunkStart;
      dataLength = Math.min(chunkSize, arrayBuffer.byteLength - chunkStart);
    }
    offset = chunkStart + chunkSize + (chunkSize % 2);
  }

  if (!dataOffset || !dataLength || channels === 0 || sampleRate === 0) return null;

  const bytesPerSample = bitsPerSample / 8;
  const frameBytes = channels * bytesPerSample;
  const numFrames = Math.floor(dataLength / frameBytes);
  const samples = new Float32Array(numFrames);

  if (audioFormat === 1 && bitsPerSample === 16) {
    for (let i = 0; i < numFrames; i++) {
      let sum = 0;
      const frameStart = dataOffset + i * frameBytes;
      for (let c = 0; c < channels; c++) {
        sum += view.getInt16(frameStart + c * 2, true);
      }
      samples[i] = (sum / channels) / 32768.0;
    }
  } else if (audioFormat === 1 && bitsPerSample === 24) {
    for (let i = 0; i < numFrames; i++) {
      let sum = 0;
      const frameStart = dataOffset + i * frameBytes;
      for (let c = 0; c < channels; c++) {
        const b0 = view.getUint8(frameStart + c * 3);
        const b1 = view.getUint8(frameStart + c * 3 + 1);
        const b2 = view.getUint8(frameStart + c * 3 + 2);
        let val = b0 | (b1 << 8) | (b2 << 16);
        if (val & 0x800000) val |= 0xff000000;
        sum += val;
      }
      samples[i] = (sum / channels) / 8388608.0;
    }
  } else if (audioFormat === 1 && bitsPerSample === 32) {
    for (let i = 0; i < numFrames; i++) {
      let sum = 0;
      const frameStart = dataOffset + i * frameBytes;
      for (let c = 0; c < channels; c++) {
        sum += view.getInt32(frameStart + c * 4, true);
      }
      samples[i] = (sum / channels) / 2147483648.0;
    }
  } else if (audioFormat === 3 && bitsPerSample === 32) {
    for (let i = 0; i < numFrames; i++) {
      let sum = 0;
      const frameStart = dataOffset + i * frameBytes;
      for (let c = 0; c < channels; c++) {
        sum += view.getFloat32(frameStart + c * 4, true);
      }
      samples[i] = sum / channels;
    }
  } else {
    return null;
  }

  return { samples, sampleRate };
}

/**
 * High-contrast colormap (Deep navy -> purple -> red -> orange -> yellow -> white)
 */
function getSpectrogramRgb(val) {
  // val is 0.0 to 1.0
  const t = Math.max(0.0, Math.min(1.0, val));
  let r, g, b;

  if (t < 0.2) {
    // 0.0 - 0.2: Deep navy to violet (0, 10, 40) -> (75, 0, 115)
    const factor = t / 0.2;
    r = Math.round(10 + 65 * factor);
    g = Math.round(15 - 15 * factor);
    b = Math.round(45 + 70 * factor);
  } else if (t < 0.45) {
    // 0.2 - 0.45: Violet to rich magenta/red (75, 0, 115) -> (200, 20, 60)
    const factor = (t - 0.2) / 0.25;
    r = Math.round(75 + 125 * factor);
    g = Math.round(20 * factor);
    b = Math.round(115 - 55 * factor);
  } else if (t < 0.75) {
    // 0.45 - 0.75: Red to vibrant orange/gold (200, 20, 60) -> (255, 160, 0)
    const factor = (t - 0.45) / 0.3;
    r = Math.round(200 + 55 * factor);
    g = Math.round(20 + 140 * factor);
    b = Math.round(60 - 60 * factor);
  } else if (t < 0.92) {
    // 0.75 - 0.92: Gold to bright yellow (255, 160, 0) -> (255, 245, 100)
    const factor = (t - 0.75) / 0.17;
    r = 255;
    g = Math.round(160 + 85 * factor);
    b = Math.round(100 * factor);
  } else {
    // 0.92 - 1.0: Yellow to bright white
    const factor = (t - 0.92) / 0.08;
    r = 255;
    g = 255;
    b = Math.round(100 + 155 * factor);
  }

  return [r, g, b];
}

/**
 * Render high-contrast spectrogram image DataURL (PNG) from WAV Blob or ArrayBuffer.
 * Returns empty string if in headless environment or invalid audio.
 */
export async function renderSpectrogramDataUrl(wavInput, options = {}) {
  try {
    let arrayBuffer = null;
    if (wavInput instanceof Blob) {
      arrayBuffer = await wavInput.arrayBuffer();
    } else if (wavInput instanceof ArrayBuffer) {
      arrayBuffer = wavInput;
    } else if (wavInput?.buffer instanceof ArrayBuffer) {
      arrayBuffer = wavInput.buffer;
    } else {
      return '';
    }

    const decoded = parseWavHeader(arrayBuffer);
    if (!decoded || !decoded.samples || decoded.samples.length < FFT_SIZE) {
      return '';
    }

    const { samples, sampleRate } = decoded;

    // Check if Canvas is supported (graceful fallback in jsdom/headless)
    if (typeof document === 'undefined') return '';
    const canvas = document.createElement('canvas');
    if (!canvas || !canvas.getContext) return '';
    const ctx = canvas.getContext('2d');
    if (!ctx) return '';

    const width = options.width || 720;
    const height = options.height || 280;
    canvas.width = width;
    canvas.height = height;

    const padLeft = 52;
    const padRight = 16;
    const padTop = 14;
    const padBottom = 32;
    const plotW = width - padLeft - padRight;
    const plotH = height - padTop - padBottom;

    if (plotW <= 0 || plotH <= 0) return '';

    // Max frequency to display (default 8000 Hz or Nyquist)
    const maxFreq = Math.min(8000, sampleRate / 2);
    const maxBin = Math.max(1, Math.min(Math.floor((maxFreq * FFT_SIZE) / sampleRate), FFT_SIZE / 2));

    // STFT Spectrogram computation
    const hopSize = Math.max(32, Math.floor((samples.length - FFT_SIZE) / Math.max(plotW, 1)));
    const numFrames = Math.max(1, Math.floor((samples.length - FFT_SIZE) / hopSize) + 1);

    const specCols = [];
    let globalMaxDb = -120;
    let globalMinDb = 0;

    const realBuf = new Float32Array(FFT_SIZE);
    const imagBuf = new Float32Array(FFT_SIZE);

    for (let fIdx = 0; fIdx < numFrames; fIdx++) {
      const start = fIdx * hopSize;
      for (let i = 0; i < FFT_SIZE; i++) {
        realBuf[i] = (samples[start + i] || 0) * HANN_WINDOW[i];
        imagBuf[i] = 0.0;
      }

      radix2Fft(realBuf, imagBuf);

      const dbs = new Float32Array(maxBin);
      for (let k = 0; k < maxBin; k++) {
        const mag = Math.sqrt(realBuf[k] * realBuf[k] + imagBuf[k] * imagBuf[k]);
        const db = 20.0 * Math.log10(mag + 1e-6);
        dbs[k] = db;
        if (db > globalMaxDb) globalMaxDb = db;
      }
      specCols.push(dbs);
    }

    const minDisplayDb = Math.max(-100, globalMaxDb - 65);
    const dbRange = Math.max(10, globalMaxDb - minDisplayDb);

    // Background fill (card style)
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, height);

    // Plot background
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(padLeft, padTop, plotW, plotH);

    // Draw spectrogram onto an ImageData buffer
    const imgData = ctx.createImageData(plotW, plotH);
    const data = imgData.data;

    for (let px = 0; px < plotW; px++) {
      const frameIndex = Math.min(Math.floor((px / plotW) * numFrames), numFrames - 1);
      const colDbs = specCols[frameIndex];

      for (let py = 0; py < plotH; py++) {
        // py = 0 is top (max frequency), py = plotH - 1 is bottom (0 Hz)
        const freqRatio = (plotH - 1 - py) / (plotH - 1);
        const binIndex = Math.min(Math.floor(freqRatio * maxBin), maxBin - 1);
        const db = colDbs[binIndex];
        const normalized = Math.max(0.0, Math.min(1.0, (db - minDisplayDb) / dbRange));
        const [r, g, b] = getSpectrogramRgb(normalized);

        const pIdx = (py * plotW + px) * 4;
        data[pIdx] = r;
        data[pIdx + 1] = g;
        data[pIdx + 2] = b;
        data[pIdx + 3] = 255;
      }
    }

    ctx.putImageData(imgData, padLeft, padTop);

    // Draw frequency axes and grid lines
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.strokeRect(padLeft, padTop, plotW, plotH);

    ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    const freqSteps = [0, 2000, 4000, 6000, 8000].filter((f) => f <= maxFreq);
    for (const freq of freqSteps) {
      const y = padTop + plotH - (freq / maxFreq) * plotH;

      // Subtle grid line
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(padLeft + plotW, y);
      ctx.stroke();
      ctx.setLineDash([]);

      const label = freq === 0 ? '0' : `${Math.round(freq / 1000)}k`;
      ctx.fillText(label, padLeft - 6, y);
    }

    // Draw time axis
    const totalDurationSec = samples.length / sampleRate;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    const timeTickCount = Math.min(5, Math.max(2, Math.floor(plotW / 120)));
    for (let t = 0; t <= timeTickCount; t++) {
      const ratio = t / timeTickCount;
      const x = padLeft + ratio * plotW;
      const timeVal = (ratio * totalDurationSec).toFixed(2);

      // Tick mark
      ctx.strokeStyle = '#94a3b8';
      ctx.beginPath();
      ctx.moveTo(x, padTop + plotH);
      ctx.lineTo(x, padTop + plotH + 4);
      ctx.stroke();

      ctx.fillText(`${timeVal}s`, x, padTop + plotH + 7);
    }

    return canvas.toDataURL('image/png');
  } catch (err) {
    console.warn('[renderSpectrogramDataUrl] fallback due to:', err);
    return '';
  }
}
