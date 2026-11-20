<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';

	const service = $derived(page.params.service ?? '');
	const tools = $derived([
		{
			href: resolve('/(app)/[service=service]/tools/logs', { service }),
			label: 'Logs',
			text: 'The newest log lines, filtered by level, lane or text, or followed live.'
		},
		{
			href: resolve('/(app)/[service=service]/tools/health', { service }),
			label: 'Health',
			text: 'Diagnose the running session, run doctor, or benchmark the api.'
		},
		{
			href: resolve('/(app)/[service=service]/tools/backtest', { service }),
			label: 'Backtest',
			text: 'Simulate the strategy or replay a session offline.'
		},
		{
			href: resolve('/(app)/[service=service]/tools/export', { service }),
			label: 'Export',
			text: 'Download sessions as CSV, or one session as JSON.'
		},
		{
			href: resolve('/(app)/[service=service]/control', { service }),
			label: 'Update',
			text: 'Pull the latest code and restart, in the Control page danger zone.'
		}
	]);
</script>

<svelte:head><title>Tools · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Tools</h1>
	<ul class="grid gap-3 sm:grid-cols-2">
		{#each tools as tool (tool.href)}
			<li>
				<a
					href={tool.href}
					class="block min-h-11 rounded-lg border border-line bg-surface p-4 hover:bg-raised focus-visible:outline-2 focus-visible:outline-focus"
				>
					<span class="font-semibold">{tool.label}</span>
					<span class="mt-1 block text-sm text-muted">{tool.text}</span>
				</a>
			</li>
		{/each}
	</ul>
</div>
