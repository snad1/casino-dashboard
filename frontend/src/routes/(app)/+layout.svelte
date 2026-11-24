<script lang="ts">
	import type { Snippet } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Ticker from '#components/Ticker';
	import { liveFor } from '#lib/live/index';
	import { can, laneScope } from '#lib/permissions';
	import { DEFAULT_SERVICE, session, signOut } from '#lib/session.svelte';
	import Toasts from '#ui/Toasts';

	let { children }: { children: Snippet } = $props();

	const service = $derived(page.params.service ?? DEFAULT_SERVICE);
	const tgUid = $derived(session.me?.tg_uid ?? null);
	const laneOnly = $derived(laneScope(session.me, 'view', service) !== null);
	const LANE_PAGES = new Set(['Live', 'Analytics', 'Sessions']);
	const tgLinks = $derived(
		tgUid === null
			? []
			: [
					{ href: resolve('/(app)/tg/[uid=uid]', { uid: String(tgUid) }), label: 'My bot' },
					{
						href: resolve('/(app)/tg/[uid=uid]/sessions', { uid: String(tgUid) }),
						label: 'Sessions'
					},
					{
						href: resolve('/(app)/tg/[uid=uid]/settings', { uid: String(tgUid) }),
						label: 'Settings'
					}
				]
	);
	const serviceLinks = $derived([
		{ href: resolve('/(app)/[service=service]', { service }), label: 'Live', show: true },
		{
			href: resolve('/(app)/[service=service]/analytics', { service }),
			label: 'Analytics',
			show: true
		},
		{
			href: resolve('/(app)/[service=service]/sessions', { service }),
			label: 'Sessions',
			show: true
		},
		{
			href: resolve('/(app)/[service=service]/control', { service }),
			label: 'Control',
			show: true
		},
		{ href: resolve('/(app)/[service=service]/config', { service }), label: 'Config', show: true },
		{
			href: resolve('/(app)/[service=service]/lanes', { service }),
			label: 'Lanes',
			show: true
		},
		{
			href: resolve('/(app)/[service=service]/tools', { service }),
			label: 'Tools',
			show: true
		},
		{
			href: resolve('/(app)/[service=service]/keys', { service }),
			label: 'Keys',
			show: can(session.me, 'keys.manage', service)
		},
		{
			href: resolve('/(app)/[service=service]/terminal', { service }),
			label: 'Terminal',
			show: true
		},
		{ href: resolve('/(app)/alerts'), label: 'Alerts', show: true },
		{ href: resolve('/(app)/admin/users'), label: 'Users', show: can(session.me, 'users.manage') },
		{ href: resolve('/(app)/admin/audit'), label: 'Audit', show: can(session.me, 'audit.read') },
		{ href: resolve('/(app)/admin/status'), label: 'Status', show: can(session.me, 'audit.read') },
		{
			href: resolve('/(app)/tg/users'),
			label: 'Telegram users',
			show: can(session.me, 'users.manage')
		}
	]);
	const links = $derived(
		tgUid !== null
			? tgLinks
			: serviceLinks.filter((l) => l.show && (!laneOnly || LANE_PAGES.has(l.label)))
	);
	const serviceTabs = $derived([
		{ href: resolve('/(app)/[service=service]', { service }), label: 'Live' },
		{ href: resolve('/(app)/[service=service]/sessions', { service }), label: 'Sessions' },
		{ href: resolve('/(app)/[service=service]/control', { service }), label: 'Control' },
		{ href: resolve('/(app)/alerts'), label: 'Alerts' }
	]);
	const tabs = $derived(
		tgUid !== null
			? tgLinks
			: laneOnly
				? serviceTabs.filter((t) => LANE_PAGES.has(t.label))
				: serviceTabs
	);

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
		{#if page.params.service}
			<Ticker live={liveFor(page.params.service)} service={page.params.service} />
		{:else}
			<span class="font-semibold">Casino dashboard</span>
		{/if}
		<div class="flex items-center gap-3 text-sm">
			<a href={resolve('/(app)/totp')} class="hidden text-muted underline sm:inline"
				>{session.me?.email ?? 'Telegram user'}</a
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
				{#each links as link (link.href)}
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
		<ul
			class="grid {tabs.length === 2
				? 'grid-cols-2'
				: tabs.length === 3
					? 'grid-cols-3'
					: 'grid-cols-4'}"
		>
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
	<Toasts />
</div>
