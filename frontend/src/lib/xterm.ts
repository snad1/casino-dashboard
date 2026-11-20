import type { ITheme } from '@xterm/xterm';

/**
 * xterm.js and its addons, fetched only when a terminal or a replay is shown, so no other
 * page's first load carries them.
 */
export async function loadXterm() {
	const [{ Terminal }, { FitAddon }, { SearchAddon }, { WebLinksAddon }] = await Promise.all([
		import('@xterm/xterm'),
		import('@xterm/addon-fit'),
		import('@xterm/addon-search'),
		import('@xterm/addon-web-links'),
		import('@xterm/xterm/css/xterm.css')
	]);
	return { Terminal, FitAddon, SearchAddon, WebLinksAddon };
}

/** The terminal's colours from the theme tokens, so it follows light and dark. */
export function xtermTheme(): ITheme {
	const token = (name: string) =>
		getComputedStyle(document.documentElement).getPropertyValue(name).trim();
	return {
		background: token('--color-canvas'),
		foreground: token('--color-ink'),
		cursor: token('--color-accent'),
		cursorAccent: token('--color-canvas'),
		selectionBackground: `${token('--color-accent')}55`
	};
}
