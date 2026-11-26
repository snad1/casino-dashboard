<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { services, type ServiceInfo } from '#lib/services.svelte';

	const current = $derived(page.params.service ?? services.picked());
	const shown = $derived(services.info(current ?? undefined));

	function state(service: ServiceInfo): string | null {
		if (service.installed === false) return 'not installed';
		return service.live ? null : 'no events';
	}

	/** Under a service's page, open the same page for the picked one; elsewhere stay put. */
	async function choose(name: string) {
		services.pick(name);
		const route = page.route.id;
		if (page.params.service && route?.startsWith('/(app)/[service=service]')) {
			await goto(resolve(route as '/(app)/[service=service]', { ...page.params, service: name }));
		}
	}
</script>

{#if services.list.length > 1 && shown}
	{@const status = state(shown)}
	<div class="flex items-center gap-2 text-sm">
		<span
			aria-hidden="true"
			class="size-2 rounded-full {status === null
				? 'bg-profit'
				: status === 'no events'
					? 'bg-warn'
					: 'bg-loss'}"
		></span>
		<span class="sr-only">{shown.label}: {status ?? 'live'}</span>
		<select
			aria-label="Service"
			value={shown.name}
			onfocus={() => void services.load()}
			onchange={(event) => void choose(event.currentTarget.value)}
			class="min-h-11 rounded-md border border-line-strong bg-surface px-2 text-ink"
		>
			{#each services.list as service (service.name)}
				{@const note = state(service)}
				<option value={service.name}>{service.label}{note ? ` (${note})` : ''}</option>
			{/each}
		</select>
	</div>
{/if}
