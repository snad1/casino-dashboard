<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import { betColumns } from '#lib/bets';
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

	const columns = $derived(betColumns(detail?.game));
	const grid = $derived(
		`grid-template-columns: 10rem 4.5rem minmax(6rem, 1fr) repeat(${columns.length}, minmax(5rem, 1fr)) minmax(6rem, 1fr) 5.5rem`
	);

	const facts = $derived(
		detail
			? [
					['Lane', String(detail.lane)],
					['Game', detail.game ?? ''],
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
	<div class="flex h-96 flex-col rounded-lg border border-line bg-surface p-2">
		<!-- A table that scrolls sideways must take focus so a keyboard can scroll it. -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<section class="min-h-0 flex-1 overflow-x-auto" aria-label="Bets" tabindex="0">
			<div class="h-full w-full min-w-max">
				<VirtualList items={bets} key={(b) => b.id} rowHeight={28} label="Bets" onend={more}>
					{#snippet header()}
						<div role="rowgroup">
							<div role="row" class="grid items-center gap-2 pb-1 text-xs text-muted" style={grid}>
								<span role="columnheader">Time</span>
								<span role="columnheader">Outcome</span>
								<span role="columnheader" class="text-right">Amount</span>
								{#each columns as column (column.label)}
									<span role="columnheader" class="text-right">{column.label}</span>
								{/each}
								<span role="columnheader" class="text-right">Profit</span>
								<span role="columnheader"></span>
							</div>
						</div>
					{/snippet}
					{#snippet row(bet: Bet)}
						{@const win = bet.state === 'win'}
						<div
							role="row"
							class="grid h-full items-center gap-2 font-mono text-xs tabular-nums"
							style={grid}
						>
							<span role="cell" class="text-muted">{new Date(bet.ts).toLocaleString()}</span>
							<span role="cell" class={win ? 'text-profit' : 'text-loss'}
								><span aria-hidden="true">{win ? '▲' : '▼'}</span> {win ? 'Win' : 'Loss'}</span
							>
							<span role="cell" class="text-right">{formatAmount(bet.amount ?? 0)}</span>
							{#each columns as column (column.label)}
								<span role="cell" class="text-right">{column.value(bet)}</span>
							{/each}
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
		</section>
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
