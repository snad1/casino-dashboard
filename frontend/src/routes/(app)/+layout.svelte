<script lang="ts">
	import type { Snippet } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { can } from '#lib/permissions';
	import { DEFAULT_SERVICE, session, signOut } from '#lib/session.svelte';

	let { children }: { children: Snippet } = $props();

	const service = $derived(page.params.service ?? DEFAULT_SERVICE);
	const links = $derived([
		{ href: resolve('/(app)/[service=service]', { service }), label: 'Live', show: true },
		{
			href: resolve('/(app)/[service=service]/analytics', { service }),
			label: 'Analytics',
			show: true
		},
		{
			href: resolve('/(app)/[service=service]/control', { service }),
			label: 'Control',
			show: true
		},
		{ href: resolve('/(app)/[service=service]/config', { service }), label: 'Config', show: true },
		{
			href: resolve('/(app)/[service=service]/terminal', { service }),
			label: 'Terminal',
			show: true
		},
		{ href: resolve('/(app)/alerts'), label: 'Alerts', show: true },
		{ href: resolve('/(app)/admin/users'), label: 'Users', show: can(session.me, 'users.manage') },
		{ href: resolve('/(app)/admin/audit'), label: 'Audit', show: can(session.me, 'audit.read') }
	]);
	const tabs = $derived([
		{ href: resolve('/(app)/[service=service]', { service }), label: 'Live' },
		{ href: resolve('/(app)/[service=service]', { service }) + '#sessions', label: 'Sessions' },
		{ href: resolve('/(app)/[service=service]/control', { service }), label: 'Control' },
		{ href: resolve('/(app)/alerts'), label: 'Alerts' }
	]);

	function current(href: string): 'page' | undefined {
		return page.url.pathname === href ? 'page' : undefined;
	}
</script>

<a
	href="#main"
	class="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-raised focus:p-2"
	>Skip to content</a
>
<div class="min-h-dvh bg-canvas text-ink">
	<header
		class="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b border-line bg-surface px-4"
	>
		<div class="flex items-baseline gap-3">
			<span class="font-semibold">Casino dashboard</span>
			<span class="text-sm text-muted">{service}</span>
		</div>
		<div class="flex items-center gap-3 text-sm">
			<a href={resolve('/(app)/totp')} class="hidden text-muted underline sm:inline"
				>{session.me?.email}</a
			>
			<button
				type="button"
				onclick={signOut}
				class="min-h-11 rounded-md px-3 text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-focus"
				>Sign out</button
			>
		</div>
	</header>
	<div class="flex">
		<nav aria-label="Main" class="hidden w-40 shrink-0 border-r border-line md:block">
			<ul class="flex flex-col gap-1 p-2">
				{#each links.filter((l) => l.show) as link (link.href)}
					<li>
						<a
							href={link.href}
							aria-current={current(link.href)}
							class="flex min-h-11 items-center rounded-md px-3 text-sm text-muted hover:bg-raised hover:text-ink aria-[current=page]:bg-raised aria-[current=page]:text-ink"
							>{link.label}</a
						>
					</li>
				{/each}
			</ul>
		</nav>
		<main id="main" class="min-w-0 flex-1 p-4 pb-24 md:pb-4">
			{@render children()}
		</main>
	</div>
	<nav
		aria-label="Tabs"
		class="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-surface md:hidden"
	>
		<ul class="grid grid-cols-4">
			<!-- eslint-disable svelte/no-navigation-without-resolve -- tab hrefs are built with resolve() above -->
			{#each tabs as tab (tab.href)}
				<li>
					<a
						href={tab.href}
						aria-current={current(tab.href)}
						class="flex min-h-14 items-center justify-center text-sm text-muted aria-[current=page]:text-ink"
						>{tab.label}</a
					>
				</li>
			{/each}
			<!-- eslint-enable svelte/no-navigation-without-resolve -->
		</ul>
	</nav>
</div>
