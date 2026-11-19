import { redirect } from '@sveltejs/kit';
import { DEFAULT_SERVICE } from '#lib/session.svelte';

export function load() {
	redirect(307, `/${DEFAULT_SERVICE}`);
}
