/**
 * Render the app icons from the logo with Playwright's Chromium: `pnpm icons`.
 * The maskable icon keeps the logo inside the central 80% that every launcher mask shows.
 */
import { readFileSync } from 'node:fs';
import { chromium } from '@playwright/test';

const logo = readFileSync('static/favicon.svg', 'utf8');
const canvas = readFileSync('src/lib/theme/tokens.css', 'utf8').match(
	/--color-canvas:\s*(#[0-9a-fA-F]{3,8})/
)[1];
const ICONS = [
	{ file: 'icon-192.png', size: 192, inset: 0 },
	{ file: 'icon-512.png', size: 512, inset: 0 },
	{ file: 'apple-touch-icon.png', size: 180, inset: 0 },
	{ file: 'maskable-512.png', size: 512, inset: 0.2 }
];

const browser = await chromium.launch();
const page = await browser.newPage();
for (const { file, size, inset } of ICONS) {
	await page.setViewportSize({ width: size, height: size });
	const pad = Math.round(size * inset * 0.5);
	await page.setContent(
		`<html><body style="margin:0;background:${canvas};width:${size}px;height:${size}px">` +
			`<div style="padding:${pad}px;box-sizing:border-box;width:${size}px;height:${size}px">` +
			logo.replace('<svg ', '<svg width="100%" height="100%" ') +
			'</div></body></html>'
	);
	await page.screenshot({ path: `static/icons/${file}`, omitBackground: false });
	console.log(`static/icons/${file} ${size}x${size}`);
}
await browser.close();
