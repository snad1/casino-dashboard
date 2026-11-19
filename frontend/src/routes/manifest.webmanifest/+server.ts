import tokens from '../../lib/theme/tokens.css?raw';
import { SERVICE_LABEL } from '#lib/service';

export const prerender = true;

const canvas = tokens.match(/--color-canvas:\s*(#[0-9a-fA-F]{3,8})/)?.[1] ?? '#0f1115';

/** The install manifest: named for this deployment's bot, coloured from the theme tokens. */
export function GET(): Response {
	const manifest = {
		name: `${SERVICE_LABEL} dashboard`,
		short_name: SERVICE_LABEL,
		description: `Live ${SERVICE_LABEL} betting bot dashboard`,
		start_url: '/',
		scope: '/',
		display: 'standalone',
		background_color: canvas,
		theme_color: canvas,
		icons: [
			{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
			{ src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
			{ src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' }
		]
	};
	return new Response(JSON.stringify(manifest), {
		headers: { 'content-type': 'application/manifest+json' }
	});
}
