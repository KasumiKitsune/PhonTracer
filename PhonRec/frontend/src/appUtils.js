export function formatPlaybackTime(seconds) {
  if (!Number.isFinite(seconds)) return '0.00s';
  return `${seconds.toFixed(2)}s`;
}

export function mergeAudioDevices(nativeDevices = [], mediaDevices = []) {
  const devices = [];
  const seen = new Set();

  const append = (device) => {
    if (!device?.id || seen.has(device.id)) return;
    seen.add(device.id);
    devices.push(device);
  };

  append({ id: 'default', name: '系统默认麦克风', is_loopback: false });
  mediaDevices
    .filter((device) => device.kind === 'audioinput' && device.deviceId !== 'default')
    .forEach((device, index) => append({
      id: device.deviceId,
      name: device.label || `麦克风 ${index + 1}`,
      is_loopback: false,
    }));
  nativeDevices.filter((device) => device.is_loopback).forEach(append);
  return devices;
}

export function selectAvailableAudioSource(preferredSource, devices) {
  return devices.some((device) => device.id === preferredSource) ? preferredSource : 'default';
}

export function buildRecordedItem(activeItem, responseData) {
  return {
    path: responseData.path,
    label: activeItem.label,
    note: activeItem.note,
    tags: activeItem.tags,
    aliases: activeItem.aliases || [],
    meta: activeItem.meta || {},
    metadata_source: activeItem.metadata_source || '录音软件',
    quality: responseData.quality,
    recorded_at: responseData.recorded_at,
    duration_ms: responseData.duration_ms,
    sample_rate_hz: responseData.sample_rate_hz,
    channels: responseData.channels,
    format: responseData.format,
    source: responseData.source,
  };
}
