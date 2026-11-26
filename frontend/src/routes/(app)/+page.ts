import { redirect } from '@sveltejs/kit';
import { services } from '#lib/services.svelte';
import { tgHome } from '#lib/tg';

export async function load({ parent }) {
	const { me } = await parent();
	const service = services.picked();
	const home = tgHome(me) ?? (service === null ? null : `/${service}`);
	if (home !== null) redirect(307, home);
}
