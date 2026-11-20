import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';
// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react(), tailwindcss()],
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
            // Only the photos the app uses, committed; the full library in media/ is not in git.
            '@media': path.resolve(__dirname, './src/assets/photos'),
        },
    },
    server: {
        port: 5173,
        host: true,
        // Editors write atomically through a temp file next to the target. Watching that
        // temp file can fail with EBUSY on Windows and takes the whole dev server down.
        watch: {
            ignored: ['**/node_modules/**', '**/.git/**', '**/.*.tmpdir/**', '**/*.tmp'],
        },
    },
});
