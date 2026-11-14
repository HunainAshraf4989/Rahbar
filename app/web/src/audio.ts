export function encodePcm(samples: Float32Array): string {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < samples.length; index++) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(index * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return btoa(String.fromCharCode(...bytes));
}

export function pcmWave(chunks: Uint8Array<ArrayBuffer>[], length: number): Blob {
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const bytes = new Uint8Array(header);
  for (const [offset, label] of [[0, 'RIFF'], [8, 'WAVEfmt '], [36, 'data']] as const) {
    bytes.set(new TextEncoder().encode(label), offset);
  }
  view.setUint32(4, 36 + length, true);
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 24000, true);
  view.setUint32(28, 48000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  view.setUint32(40, length, true);
  return new Blob([header, ...chunks], { type: 'audio/wav' });
}
