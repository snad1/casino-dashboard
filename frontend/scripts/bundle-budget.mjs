/**
 * Fail the build when any page's first load is over the gzip budget.
 *
 * A page's first load is the client entry, every layout above the page and the page itself,
 * with all their static imports and CSS. Dynamic imports (the chart library, the terminal)
 * load later and are not counted. Run after `vite build`: `pnpm budget`.
 */
import { readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';

const BUDGET_KB = Number(process.env.BUNDLE_BUDGET_KB ?? 250);
const OUT = '.svelte-kit/output/client';
const manifest = JSON.parse(readFileSync(join(OUT, '.vite/manifest.json'), 'utf8'));

const sizes = new Map();
function gzipped(file) {
	if (!sizes.has(file))
		sizes.set(file, gzipSync(readFileSync(join(OUT, file)), { level: 9 }).length);
	return sizes.get(file);
}

/** Every file a manifest entry needs before it runs: its own, its static imports, their CSS. */
function closure(key, files = new Set()) {
	const entry = manifest[key];
	if (!entry || files.has(entry.file)) return files;
	files.add(entry.file);
	for (const css of entry.css ?? []) files.add(css);
	for (const imported of entry.imports ?? []) closure(imported, files);
	return files;
}

const nodes = Object.keys(manifest)
	.filter((key) => /\/nodes\/\d+\.js$/.test(key))
	.map((key) => {
		const source = readFileSync(key, 'utf8').match(/from "([^"]+\/\+(page|layout)\.svelte)"/);
		if (!source) return null;
		const file = relative('src/routes', join(dirname(key), source[1]));
		return { key, kind: source[2], dir: dirname(file) === '.' ? '' : dirname(file) };
	})
	.filter((node) => node !== null);

const entries = Object.keys(manifest).filter(
	(key) => manifest[key].isEntry && !/\/nodes\/\d+\.js$/.test(key)
);
const within = (dir, parent) => parent === '' || dir === parent || dir.startsWith(`${parent}/`);

const rows = nodes
	.filter((node) => node.kind === 'page')
	.map((page) => {
		const files = new Set();
		for (const key of entries) closure(key, files);
		for (const layout of nodes.filter((n) => n.kind === 'layout' && within(page.dir, n.dir))) {
			closure(layout.key, files);
		}
		closure(page.key, files);
		const bytes = [...files].reduce((sum, file) => sum + gzipped(file), 0);
		return { route: `/${page.dir}`, kb: bytes / 1024 };
	})
	.sort((a, b) => b.kb - a.kb);

const over = rows.filter((row) => row.kb > BUDGET_KB);
for (const row of rows) {
	const mark = row.kb > BUDGET_KB ? 'OVER' : 'ok';
	console.log(`${mark.padEnd(4)} ${row.kb.toFixed(1).padStart(7)} KB  ${row.route}`);
}
if (over.length) {
	console.error(`\n${over.length} page(s) over the ${BUDGET_KB} KB gzip first-load budget.`);
	process.exit(1);
}
console.log(`\nEvery page is within the ${BUDGET_KB} KB gzip first-load budget.`);
