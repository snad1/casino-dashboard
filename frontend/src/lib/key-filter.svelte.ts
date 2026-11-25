import { api } from '#lib/session.svelte';

const STORE = 'casino.key';

/**
 * Which casino account (vault key) each service's pages show: `null` is every key. The
 * choice is kept in this browser per service; it only narrows what is shown and never
 * widens what the api lets a user see.
 */
class KeyFilter {
	#selected: Record<string, string | null> = $state({});
	#labels: Record<string, Record<string, string>> = $state({});

	get(service: string): string | null {
		return service in this.#selected ? (this.#selected[service] ?? null) : read(service);
	}

	set(service: string, key: string | null): void {
		this.#selected[service] = key;
		if (typeof localStorage === 'undefined') return;
		if (key === null) localStorage.removeItem(`${STORE}.${service}`);
		else localStorage.setItem(`${STORE}.${service}`, key);
	}

	/** The keys' labels the user may read; a key they may not list shows by its id. */
	label(service: string, key: string): string {
		return this.#labels[service]?.[key] ?? key;
	}

	async loadLabels(service: string): Promise<void> {
		if (service in this.#labels) return;
		this.#labels[service] = {};
		const { data } = await api.GET('/{service}/keys', { params: { path: { service } } });
		if (!data) return;
		this.#labels[service] = Object.fromEntries(
			data.keys.filter((k) => k.kind === 'key').map((k) => [k.id, k.label || k.id])
		);
	}
}

function read(service: string): string | null {
	if (typeof localStorage === 'undefined') return null;
	return localStorage.getItem(`${STORE}.${service}`);
}

export const keyFilter = new KeyFilter();
