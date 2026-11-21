import { redirect } from '@sveltejs/kit';
import { DEFAULT_SERVICE } from '#lib/session.svelte';
import { tgHome } from '#lib/tg';

export async function load({ parent }) {
	const { me } = await parent();
	redirect(307, tgHome(me) ?? `/${DEFAULT_SERVICE}`);
}
