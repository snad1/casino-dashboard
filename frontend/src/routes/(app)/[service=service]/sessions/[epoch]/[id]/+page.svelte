<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import { formatAmount, formatProfit } from '#lib/format';
	import { api } from '#lib/session.svelte';
	import Comments from '#components/Comments';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';
	import VirtualList from '#ui/VirtualList';

	type Detail = components['schemas']['SessionDetail'];
	type Bet = components['schemas']['BetOut'];

	const service = $derived(page.params.service ?? '');
	const epoch = $derived(Number(page.params.epoch));
	const id = $derived(Number(page.params.id));

	let detail = $state<Detail | null>(null);
	let bets: Bet[] = $state.raw([]);
	let before: number | null = null;
	let done = $state(false);
	let busy = false;
	let error = $state<string | null>(null);
	let betShown = $state<number | null>(null);

	const path = $derived({ service, epoch_id: epoch, session_id: id });

	async function more() {
		if (busy || done) return;
		busy = true;
		try {
			const query = before === null ? { limit: 200 } : { limit: 200, before };
			const result = await unwrap(
				api.GET('/{service}/sessions/{epoch_id}/{session_id}/bets', { params: { path, query } })
			);
			bets = [...bets, ...result.items];
			before = result.next_before ?? null;
			done = before === null;
		} catch (e) {
			error = e instanceof ApiError ? e.detail : "Couldn't load the bets. Try again.";
		} finally {
			busy = false;
		}
	}

	onMount(async () => {
		try {
			detail = await unwrap(
				api.GET('/{service}/sessions/{epoch_id}/{session_id}', { params: { path } })
			);
			await more();
		} catch (e) {
			error = e instanceof ApiError ? e.detail : "Couldn't load sessions. Try again.";
		}
	});

	const facts = $derived(
		detail
			? [
					['Lane', String(detail.lane)],
					['Strategy', detail.strategy ?? ''],
					['Started', detail.started_at ? new Date(detail.started_at).toLocaleString() : ''],
					['Ended', detail.ended_at ? new Date(detail.ended_at).toLocaleString() : 'Running'],
					['Bets', String(detail.total_bets ?? 0)],
					['Wins / losses', `${detail.wins ?? 0} / ${detail.losses ?? 0}`],
					['Profit', formatProfit(detail.profit ?? 0)],
					['Wagered', formatAmount(detail.wagered ?? 0)],
					[
						'Max win / loss streak',
						`${detail.max_win_streak ?? 0} / ${detail.max_loss_streak ?? 0}`
					],
					[
						'Balance',
						`${formatAmount(detail.start_balance ?? 0)} to ${formatAmount(detail.end_balance ?? 0)} ${detail.currency ?? ''}`
					]
				]
			: []
	);
</script>

<svelte:head><title>Session {id} · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-5xl flex-col gap-4">
	<a href={resolve('/(app)/[service=service]', { service })} class="text-sm text-accent underline"
		>Back to the dashboard</a
	>
	<h1 class="text-xl font-semibold">Session {id}</h1>
	{#if error}
		<Notice tone="error">{error}</Notice>
		<Button onclick={more}>Try again</Button>
	{/if}
	{#if detail}
		<dl class="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
			{#each facts as [label, value] (label)}
				<div>
					<dt class="text-muted">{label}</dt>
					<dd class="font-mono tabular-nums">{value}</dd>
				</div>
			{/each}
		</dl>
	{/if}
	<div class="h-96 rounded-lg border border-line bg-surface p-2">
		<VirtualList items={bets} key={(b) => b.id} rowHeight={28} label="Bets" onend={more}>
			{#snippet row(bet: Bet)}
				{@const win = bet.state === 'win'}
				<div
					role="row"
					class="grid h-full grid-cols-[10rem_4.5rem_1fr_4rem_4rem_1fr_5.5rem] items-center gap-2 font-mono text-xs tabular-nums"
				>
					<span role="cell" class="text-muted">{new Date(bet.ts).toLocaleString()}</span>
					<span role="cell" class={win ? 'text-profit' : 'text-loss'}
						><span aria-hidden="true">{win ? '▲' : '▼'}</span> {win ? 'Win' : 'Loss'}</span
					>
					<span role="cell" class="text-right">{formatAmount(bet.amount ?? 0)}</span>
					<span role="cell" class="text-right">{(bet.multiplier ?? 0).toFixed(2)}x</span>
					<span role="cell" class="text-right">{bet.result_value ?? ''}</span>
					<span role="cell" class="text-right {win ? 'text-profit' : 'text-loss'}"
						>{formatProfit(bet.profit ?? 0)}</span
					>
					<span role="cell" class="text-right">
						<button
							type="button"
							aria-pressed={betShown === bet.id}
							aria-label="Comments on bet {bet.id}"
							onclick={() => (betShown = betShown === bet.id ? null : bet.id)}
							class="h-6 rounded-sm border border-line-strong px-2 font-sans text-accent aria-pressed:bg-raised"
							>Comments</button
						>
					</span>
				</div>
			{/snippet}
		</VirtualList>
	</div>
	{#if detail}
		<div class="grid gap-4 md:grid-cols-2">
			<div class="rounded-lg border border-line bg-surface p-4">
				<Comments
					{service}
					target={{ kind: 'session', epoch_id: epoch, session_id: id }}
					title="Session comments"
				/>
			</div>
			<div class="rounded-lg border border-line bg-surface p-4">
				{#if betShown === null}
					<p class="text-sm text-muted">Pick "Comments" on a bet to see or add its comments.</p>
				{:else}
					{#key betShown}
						<Comments
							{service}
							target={{ kind: 'bet', epoch_id: epoch, bet_id: betShown }}
							title="Comments on bet {betShown}"
						/>
					{/key}
				{/if}
			</div>
		</div>
	{/if}
</div>
