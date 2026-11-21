<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import { formatProfit } from '#lib/format';
	import { api } from '#lib/session.svelte';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';

	type TgUser = components['schemas']['TgUserOut'];
	const LOAD_FAILED = "Couldn't load the Telegram users. Try again.";

	let users: TgUser[] | null = $state(null);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			users = (await unwrap(api.GET('/tg/users'))).items;
		} catch (e) {
			error = e instanceof ApiError && e.detail ? e.detail : LOAD_FAILED;
		}
	});
</script>

<svelte:head><title>Telegram users · Casino dashboard</title></svelte:head>

<Guard perm="users.manage">
	<div class="mx-auto flex max-w-4xl flex-col gap-4">
		<h1 class="text-xl font-semibold">Telegram users</h1>
		{#if error}
			<Notice tone="error">{error}</Notice>
		{:else if users === null}
			<p class="text-sm text-muted" role="status">Loading Telegram users.</p>
		{:else if users.length === 0}
			<p class="text-sm text-muted">No Telegram user has betting history yet.</p>
		{:else}
			<table class="w-full text-sm">
				<thead class="text-left text-muted">
					<tr>
						<th class="py-2">Telegram id</th>
						<th>Sessions</th>
						<th>Bets</th>
						<th>Profit</th>
						<th>Last session</th>
					</tr>
				</thead>
				<tbody>
					{#each users as user (user.uid)}
						<tr class="border-t border-line">
							<td class="py-2">
								<a
									href={resolve('/(app)/tg/[uid=uid]', { uid: String(user.uid) })}
									class="text-accent underline">{user.uid}</a
								>
							</td>
							<td class="tabular-nums">{user.sessions}</td>
							<td class="tabular-nums">{user.bets}</td>
							<td class="font-mono tabular-nums {user.profit < 0 ? 'text-loss' : 'text-profit'}"
								>{formatProfit(user.profit)}</td
							>
							<td>{user.last_session_at ? new Date(user.last_session_at).toLocaleString() : ''}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</Guard>
