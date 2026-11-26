import { redirect } from '@sveltejs/kit';
import { services } from '#lib/services.svelte';
import { loadMe, loginUrl } from '#lib/session.svelte';

export async function load({ url }) {
	const me = await loadMe();
	if (me === null) redirect(307, loginUrl(url.pathname + url.search));
	await services.load();
	return { me };
}
