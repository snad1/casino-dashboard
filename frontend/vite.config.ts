import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			adapter: adapter({ fallback: 'index.html', strict: true }),
			compilerOptions: { runes: true },
			csp: { mode: 'hash', directives: { 'script-src': ['self'] } }
		})
	],
	server: {
		proxy: {
			'/api': {
				target: 'http://localhost:8010',
				rewrite: (path) => path.replace(/^\/api/, '')
			},
			'/ws': { target: 'ws://localhost:8010', ws: true }
		}
	},
	test: {
		include: ['src/**/*.test.ts'],
		environment: 'node'
	}
});
