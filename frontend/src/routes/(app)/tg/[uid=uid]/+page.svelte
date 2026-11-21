<script lang="ts">
	import { page } from '$app/state';
	import BetTape from '#components/BetTape';
	import Chart from '#components/Chart';
	import LaneCard from '#components/LaneCard';
	import { ApiError, unwrap } from '#lib/api/client';
	import { formatProfit } from '#lib/format';
	import { tgSource } from '#lib/history-source';
	import { liveFor } from '#lib/live/index';
	import { api } from '#lib/session.svelte';
	import { ACTION_COPY, TG_SERVICE, type TgAction } from '#lib/tg';
	import { tgControl } from '#lib/tg-control.svelte';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';

	type Running = { slot: number; session_id?: number; paused?: boolean; profit?: number };

	const STATUS_FAILED = "Couldn't ask the bot for its sessions. Try again.";
	const uid = $derived(Number(page.params.uid));
	const live = liveFor(TG_SERVICE);
	const source = $derived(tgSource(uid));

	let running: Running[] = $state.raw([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function loadStatus(user: number) {
		loading = true;
		error = null;
		try {
			const status = await unwrap(api.GET('/tg/{uid}/status', { params: { path: { uid: user } } }));
			running = status.sessions as Running[];
		} catch (e) {
			error = e instanceof ApiError && e.detail ? e.detail : STATUS_FAILED;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void tgControl.done;
		void loadStatus(uid);
	});

	const actions = (s: Running): TgAction[] => (s.paused ? ['resume', 'stop'] : ['pause', 'stop']);
</script>

<svelte:head><title>My bot · Casino dashboard</title></svelte:head>

<div class="flex flex-col gap-4">
	<h1 class="text-xl font-semibold">My bot</h1>
	<section aria-labelledby="running-title" class="rounded-lg border border-line bg-surface p-4">
		<div class="flex items-center justify-between gap-2">
			<h2 id="running-title" class="text-sm font-semibold text-muted">Running sessions</h2>
			<Button variant="primary" onclick={() => tgControl.ask(uid, 'start')}
				>{ACTION_COPY.start.label}</Button
			>
		</div>
		{#if loading}
			<p class="mt-2 text-sm text-muted" role="status">Asking the bot.</p>
		{:else if error}
			<div class="mt-2"><Notice tone="error">{error}</Notice></div>
		{:else if running.length === 0}
			<p class="mt-2 text-sm text-muted">No session is running.</p>
		{:else}
			<ul class="mt-2 flex flex-col gap-2">
				{#each running as s (s.slot)}
					<li class="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2">
						<span class="text-sm"
							>Slot {s.slot}{s.session_id ? ` · session #${s.session_id}` : ''} ·
							{s.paused ? 'Paused' : 'Running'} · {formatProfit(s.profit ?? 0)}</span
						>
						<span class="flex gap-2">
							{#each actions(s) as action (action)}
								<Button
									variant={action === 'stop' ? 'danger' : 'secondary'}
									onclick={() => tgControl.ask(uid, action, s.slot)}
									>{ACTION_COPY[action].label}</Button
								>
							{/each}
						</span>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
	<div class="grid grid-cols-1 gap-4 lg:grid-cols-12">
		<section
			aria-labelledby="lane-title"
			class="rounded-lg border border-line bg-surface p-4 lg:col-span-4"
		>
			<h2 id="lane-title" class="text-sm font-semibold text-muted">Live</h2>
			<div class="mt-2 flex flex-col gap-3">
				{#each Object.entries(live.lanes) as [n, lane] (n)}
					<LaneCard n={Number(n)} {lane} />
				{:else}
					<p class="text-sm text-muted">Waiting for live data.</p>
				{/each}
			</div>
		</section>
		<section
			aria-labelledby="chart-title"
			class="rounded-lg border border-line bg-surface p-4 lg:col-span-8"
		>
			<h2 id="chart-title" class="text-sm font-semibold text-muted">Chart</h2>
			<div class="mt-2 h-80">
				{#key uid}
					<Chart {live} service={TG_SERVICE} {source} />
				{/key}
			</div>
		</section>
		<section
			aria-labelledby="tape-title"
			class="rounded-lg border border-line bg-surface p-4 lg:col-span-12"
		>
			<h2 id="tape-title" class="text-sm font-semibold text-muted">Bet tape</h2>
			<div class="mt-2 h-96"><BetTape {live} /></div>
		</section>
	</div>
</div>
