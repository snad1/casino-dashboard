import { redirect } from '@sveltejs/kit';
import { loadMe, loginUrl } from '#lib/session.svelte';

export async function load({ url }) {
	const me = await loadMe();
	if (me === null) redirect(307, loginUrl(url.pathname + url.search));
	return { me };
}
