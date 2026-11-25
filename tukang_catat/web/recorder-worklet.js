// Mengubah audio mikrofon (biasanya 44.1/48 kHz) menjadi 16 kHz mono float32.
// Downsampling dengan rata-rata per jendela (low-pass sederhana).
const TARGET_RATE = 16000;

class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.pending = new Float32Array(0);
    this.consumed = 0; // jumlah sampel input yang sudah dibuang dari `pending`
    this.nextOut = 0;  // indeks sampel output berikutnya
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    const buf = new Float32Array(this.pending.length + ch.length);
    buf.set(this.pending);
    buf.set(ch, this.pending.length);

    const out = [];
    let sumSq = 0;
    while (Math.floor((this.nextOut + 1) * this.ratio) - this.consumed <= buf.length) {
      const a = Math.floor(this.nextOut * this.ratio) - this.consumed;
      const b = Math.max(a + 1, Math.floor((this.nextOut + 1) * this.ratio) - this.consumed);
      let s = 0;
      for (let i = a; i < b; i++) s += buf[i];
      const v = s / (b - a);
      out.push(v);
      sumSq += v * v;
      this.nextOut++;
    }
    const drop = Math.floor(this.nextOut * this.ratio) - this.consumed;
    this.pending = buf.slice(drop);
    this.consumed += drop;

    if (out.length) {
      const arr = Float32Array.from(out);
      this.port.postMessage({ samples: arr, rms: Math.sqrt(sumSq / out.length) }, [arr.buffer]);
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
