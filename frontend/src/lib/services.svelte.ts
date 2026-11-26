import type { components } from '#lib/api/schema';
import { api } from '#lib/session.svelte';

export type ServiceInfo = components['schemas']['ServiceOut'];

const STORE = 'casino.service';

/**
 * The services the signed-in user may open, as the api lists them, and the one they last
 * picked in this browser. The pick only chooses what is shown; the api still checks each page.
 */
class Services {
	list: ServiceInfo[] = $state([]);
	#stored: string | null = $state(read());

	async load(): Promise<void> {
		const { data } = await api.GET('/services');
		this.list = data ?? [];
	}

	info(name: string | undefined): ServiceInfo | undefined {
		return this.list.find((s) => s.name === name);
	}

	/** The stored pick when the user may still open it, else their first service, else null. */
	picked(): string | null {
		return (this.info(this.#stored ?? undefined) ?? this.list[0])?.name ?? null;
	}

	pick(name: string): void {
		if (!this.info(name) || name === this.#stored) return;
		this.#stored = name;
		if (typeof localStorage !== 'undefined') localStorage.setItem(STORE, name);
	}
}

function read(): string | null {
	if (typeof localStorage === 'undefined') return null;
	return localStorage.getItem(STORE);
}

export const services = new Services();
