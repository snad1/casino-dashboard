<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import BarChart from '#components/BarChart';
	import {
		exportUrl,
		filterQuery,
		formatCount,
		formatDuration,
		formatTime,
		hourLabel,
		LANES,
		parseFilter,
		winRate,
		type AnalyticsOut,
		type Filter
	} from '#lib/analytics';
	import { ApiError, unwrap } from '#lib/api/client';
	import { formatAmount, formatProfit } from '#lib/format';
	import { denialReason } from '#lib/permissions';
	import { api, session } from '#lib/session.svelte';
	import Notice from '#ui/Notice';

	const service = $derived(page.params.service ?? '');
	const filter = $derived(parseFilter(page.url.searchParams));
	const exportReason = $derived(denialReason(session.me, 'export', service));

	let data = $state<AnalyticsOut | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let since = $state('');
	let until = $state('');
	let lane = $state('all');
	let request = 0;

	$effect(() => {
		since = filter.since ?? '';
		until = filter.until ?? '';
		lane = filter.lane === null ? 'all' : String(filter.lane);
		if (service) void load(service, filter);
	});

	async function load(name: string, wanted: Filter) {
		const mine = ++request;
		loading = true;
		try {
			const out = await unwrap(
				api.GET('/{service}/analytics', {
					params: { path: { service: name }, query: filterQuery(wanted) }
				})
			);
			if (mine !== request) return;
			data = out;
			error = null;
		} catch (e) {
			if (mine !== request) return;
			data = null;
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			if (mine === request) loading = false;
		}
	}

	function apply(event: SubmitEvent) {
		event.preventDefault();
		const query = new URLSearchParams(
			filterQuery({
				since: since || null,
				until: until || null,
				lane: lane === 'all' ? null : Number(lane)
			})
		).toString();
		const suffix = query ? `?${query}` : '';
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- this page plus its own filter query
		void goto(resolve('/(app)/[service=service]/analytics', { service }) + suffix);
	}

	const amount = (value: number | null | undefined) => formatAmount(value ?? 0);
	const signed = (value: number | null | undefined) => formatProfit(value ?? 0);
	const tone = (value: number | null | undefined) =>
		(value ?? 0) < 0 ? 'text-loss' : 'text-profit';

	const report = $derived(data?.report ?? null);
	const life = $derived(report?.lifetime ?? null);
	const groups = $derived(
		report
			? ([
					...(report.by_lane.length > 1
						? [
								{
									title: 'By lane',
									key: 'Lane',
									rows: report.by_lane.map((r) => ({ ...r, name: r.lane }))
								}
							]
						: []),
					{
						title: 'By game',
						key: 'Game',
						rows: report.by_game.map((r) => ({ ...r, name: r.game }))
					},
					{
						title: 'By strategy',
						key: 'Strategy',
						rows: report.by_strategy.map((r) => ({ ...r, name: r.strategy }))
					}
				] as const)
			: []
	);
	const tops = $derived(
		report
			? [
					{ title: 'Top 5 best sessions', rows: report.top_best },
					{ title: 'Top 5 worst sessions', rows: report.top_worst }
				]
			: []
	);
	const charts = $derived(data?.charts ?? null);

	function bin(lo: number, hi: number, show: (v: number) => string) {
		return `${show(lo)} to ${show(hi)}`;
	}

	const cell = 'p-2 text-right font-mono tabular-nums';
	const input = 'min-h-11 rounded-md border border-line-strong bg-surface px-3';
</script>

<svelte:head><title>Analytics · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-6xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Analytics</h1>

	<form
		onsubmit={apply}
		class="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface p-4"
	>
		<label class="flex flex-col gap-1 text-sm font-medium">
			From
			<input type="date" class={input} bind:value={since} max={until || undefined} />
		</label>
		<label class="flex flex-col gap-1 text-sm font-medium">
			To
			<input type="date" class={input} bind:value={until} min={since || undefined} />
		</label>
		<label class="flex flex-col gap-1 text-sm font-medium">
			Lane
			<select class={input} bind:value={lane}>
				<option value="all">All</option>
				{#each LANES as n (n)}
					<option value={String(n)}>Lane {n}</option>
				{/each}
			</select>
		</label>
		<button
			type="submit"
			class="min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-on-accent">Apply</button
		>
	</form>

	{#if error}
		<Notice tone="error">{error}</Notice>
	{:else if loading && !data}
		<p class="text-sm text-muted" aria-live="polite">Loading analytics...</p>
	{/if}

	{#if data?.source === 'history'}
		<Notice>Bot unreachable: numbers rebuilt from history.</Notice>
	{/if}

	{#if report && life}
		<section
			class="rounded-lg border border-line bg-surface p-4"
			aria-labelledby="overall-title"
			aria-busy={loading}
		>
			<h2 id="overall-title" class="font-semibold">Overall</h2>
			{#if life.sessions_total === 0}
				<p class="mt-2 text-sm text-muted">No sessions in this range.</p>
			{:else}
				<dl class="mt-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
					{#if report.span.first}
						<dt class="text-muted">Time range</dt>
						<dd class="font-mono">
							{formatTime(report.span.first)} to {formatTime(report.span.last)}
						</dd>
					{/if}
					<dt class="text-muted">Time span</dt>
					<dd class="font-mono">{formatDuration(report.span.span_sec)}</dd>
					<dt class="text-muted">Bet time</dt>
					<dd class="font-mono">{formatDuration(report.span.bet_sec)}</dd>
					<dt class="text-muted">Total sessions</dt>
					<dd class="font-mono tabular-nums">{formatCount(life.sessions_total)}</dd>
					<dt class="text-muted">Sessions in profit</dt>
					<dd class="font-mono tabular-nums">{formatCount(life.sessions_in_profit)}</dd>
					<dt class="text-muted">Sessions in loss</dt>
					<dd class="font-mono tabular-nums">{formatCount(life.sessions_in_loss)}</dd>
					<dt class="text-muted">Total bets</dt>
					<dd class="font-mono tabular-nums">
						{formatCount(life.bets_total)} W: {formatCount(life.wins_total)} L: {formatCount(
							life.losses_total
						)}
					</dd>
					<dt class="text-muted">Win rate</dt>
					<dd class="font-mono tabular-nums">{(life.win_rate_pct ?? 0).toFixed(1)}%</dd>
					<dt class="text-muted">Total wagered</dt>
					<dd class="font-mono tabular-nums">{amount(life.wagered_total)}</dd>
					<dt class="text-muted">Total P/L</dt>
					<dd class="font-mono tabular-nums {tone(life.profit_total)}">
						{signed(life.profit_total)}
					</dd>
					<dt class="text-muted">Best session</dt>
					<dd class="font-mono tabular-nums">{signed(life.best_profit)}</dd>
					<dt class="text-muted">Worst session</dt>
					<dd class="font-mono tabular-nums">{signed(life.worst_profit)}</dd>
				</dl>
			{/if}
		</section>

		{#each groups as group (group.title)}
			{#if group.rows.length}
				<!-- A table that scrolls sideways must take focus so a keyboard can scroll it. -->
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<section
					class="overflow-x-auto rounded-lg border border-line bg-surface p-4"
					aria-label={group.title}
					tabindex="0"
				>
					<table class="w-full text-left text-sm">
						<caption class="mb-2 text-left font-semibold">{group.title}</caption>
						<thead class="text-muted">
							<tr>
								<th scope="col" class="p-2">{group.key}</th>
								<th scope="col" class="p-2 text-right">Sessions</th>
								<th scope="col" class="p-2 text-right">Bets</th>
								<th scope="col" class="p-2 text-right">Win rate</th>
								<th scope="col" class="p-2 text-right">P/L</th>
								<th scope="col" class="p-2 text-right">Wagered</th>
							</tr>
						</thead>
						<tbody>
							{#each group.rows as row (row.name)}
								<tr class="border-t border-line">
									<th scope="row" class="p-2 font-medium">{row.name}</th>
									<td class={cell}>{formatCount(row.sessions)}</td>
									<td class={cell}>{formatCount(row.bets)}</td>
									<td class={cell}>{winRate(row.wins, row.bets)}</td>
									<td class="{cell} {tone(row.profit)}">{signed(row.profit)}</td>
									<td class={cell}>{amount(row.wagered)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</section>
			{/if}
		{/each}

		{#each tops as top (top.title)}
			{#if top.rows.length}
				<!-- A table that scrolls sideways must take focus so a keyboard can scroll it. -->
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
				<section
					class="overflow-x-auto rounded-lg border border-line bg-surface p-4"
					aria-label={top.title}
					tabindex="0"
				>
					<table class="w-full text-left text-sm">
						<caption class="mb-2 text-left font-semibold">{top.title}</caption>
						<thead class="text-muted">
							<tr>
								<th scope="col" class="p-2 text-right">ID</th>
								<th scope="col" class="p-2">Game</th>
								<th scope="col" class="p-2">Strategy</th>
								<th scope="col" class="p-2 text-right">Bets</th>
								<th scope="col" class="p-2 text-right">Win rate</th>
								<th scope="col" class="p-2 text-right">P/L</th>
								<th scope="col" class="p-2 text-right">Wagered</th>
								<th scope="col" class="p-2">Started</th>
							</tr>
						</thead>
						<tbody>
							{#each top.rows as row (row.id)}
								<tr class="border-t border-line">
									<td class={cell}>{row.id}</td>
									<td class="p-2">{row.game}</td>
									<td class="p-2">{row.strategy}</td>
									<td class={cell}>{formatCount(row.bets)}</td>
									<td class={cell}>{winRate(row.wins, row.bets)}</td>
									<td class="{cell} {tone(row.profit)}">{signed(row.profit)}</td>
									<td class={cell}>{amount(row.wagered)}</td>
									<td class="p-2 font-mono text-muted">{formatTime(row.started_at)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</section>
			{/if}
		{/each}
	{/if}

	{#if charts && report}
		<div class="grid gap-4 lg:grid-cols-2">
			<BarChart
				title="Profit by hour of day"
				valueLabel="P/L"
				signed
				bars={charts.by_hour.map((r) => ({
					label: hourLabel(r.hour),
					value: r.profit,
					text: signed(r.profit)
				}))}
			/>
			<BarChart
				title="Profit by day"
				valueLabel="P/L"
				signed
				bars={charts.by_day.map((r) => ({ label: r.day, value: r.profit, text: signed(r.profit) }))}
			/>
			<BarChart
				title="Profit per session"
				valueLabel="Sessions"
				bars={charts.profit_bins.map((r) => ({
					label: bin(r.lo, r.hi, signed),
					value: r.sessions,
					text: formatCount(r.sessions)
				}))}
			/>
			<BarChart
				title="Session length"
				valueLabel="Sessions"
				bars={charts.duration_bins.map((r) => ({
					label: bin(r.lo_sec, r.hi_sec, formatDuration),
					value: r.sessions,
					text: formatCount(r.sessions)
				}))}
			/>
			<BarChart
				title="Profit by lane"
				valueLabel="P/L"
				signed
				bars={report.by_lane.map((r) => ({
					label: r.lane,
					value: r.profit,
					text: signed(r.profit)
				}))}
			/>
		</div>
	{/if}

	<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="export-title">
		<h2 id="export-title" class="font-semibold">Export this range</h2>
		<p class="mt-1 text-sm text-muted">
			The sessions in this range from the dashboard's history copy. For the bot's own export format,
			use
			<a
				class="text-accent underline"
				href={resolve('/(app)/[service=service]/tools/export', { service })}>Tools &gt; Export</a
			>.
		</p>
		<div class="mt-3 flex flex-wrap items-center gap-3">
			{#each ['csv', 'json'] as const as format (format)}
				{#if exportReason === null}
					<!-- eslint-disable svelte/no-navigation-without-resolve -- an api download, not an app route -->
					<a
						class="inline-flex min-h-11 items-center rounded-md bg-accent px-4 text-sm font-medium text-on-accent"
						href={exportUrl(service, filter, format)}
						download>Download {format.toUpperCase()}</a
					>
					<!-- eslint-enable svelte/no-navigation-without-resolve -->
				{:else}
					<button
						type="button"
						disabled
						class="min-h-11 rounded-md border border-line-strong px-4 text-sm text-muted"
						>Download {format.toUpperCase()}</button
					>
				{/if}
			{/each}
			{#if exportReason}
				<p class="text-sm text-muted">{exportReason}</p>
			{/if}
		</div>
	</section>
</div>
