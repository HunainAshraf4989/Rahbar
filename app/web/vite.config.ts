import { copyFileSync, createReadStream, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

const target = process.env.RAHBAR_WEB_API ?? 'http://127.0.0.1:8000';
const proxy = { '/dev': { target, ws: true } };
const publicDir = '.cache/web-public';
const assets = new Map([
  ['vad.worklet.bundle.min.js', { package: '@ricky0123/vad-web', type: 'text/javascript' }],
  ['silero_vad_v5.onnx', { package: '@ricky0123/vad-web', type: 'application/octet-stream' }],
  ['ort-wasm-simd-threaded.mjs', { package: 'onnxruntime-web', type: 'text/javascript' }],
  ['ort-wasm-simd-threaded.wasm', { package: 'onnxruntime-web', type: 'application/wasm' }],
]);

export default defineConfig({
  publicDir,
  server: { proxy },
  preview: { proxy },
  build: { outDir: '.cache/dist' },
  plugins: [{
    name: 'rahbar-vad-assets',
    configResolved({ root }) {
      const output = resolve(root, publicDir, 'vad');
      mkdirSync(output, { recursive: true });
      for (const [file, asset] of assets) {
        copyFileSync(resolve(root, 'node_modules', asset.package, 'dist', file), resolve(output, file));
      }
    },
    configureServer(server) {
      // ONNX Runtime imports its own ESM binding. Serve it before Vite rewrites module requests.
      server.middlewares.use('/vad', (request, response, next) => {
        const file = request.url?.split('?')[0].slice(1) ?? '';
        const asset = assets.get(file);
        if (!asset) return next();
        response.setHeader('Content-Type', asset.type);
        createReadStream(resolve(server.config.root, publicDir, 'vad', file)).pipe(response);
      });
    },
  }],
});
