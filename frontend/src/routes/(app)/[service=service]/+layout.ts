import { redirect } from '@sveltejs/kit';
import { tgHome } from '#lib/tg';

export async function load({ parent }) {
	const home = tgHome((await parent()).me);
	if (home !== null) redirect(307, home);
}
