<script lang="ts">
	import { onMount } from 'svelte';
	import type {
		IChartApi,
		ISeriesApi,
		ISeriesMarkersPluginApi,
		LogicalRange,
		SeriesMarker,
		Time,
		UTCTimestamp
	} from 'lightweight-charts';
	import { formatProfit } from '#lib/format';
	import { serviceSource, type HistorySource } from '#lib/history-source';
	import type { LiveStore } from '#lib/live/store.svelte';
	import {
		LiveFolder,
		RES_SECONDS,
		capPoints,
		mergePoints,
		needsReload,
		rangeFor,
		toPoints,
		zoomedRange,
		type LaneSel,
		type Metric,
		type Point,
		type Preset,
		type Range,
		type Resolution
	} from '#lib/series';

	type Props = {
		live: LiveStore;
		service: string;
		source?: HistorySource;
		notes?: Array<{ at: string | null }>;
		onrange?: (range: { from: string; to: string }) => void;
		onpick?: (at: string) => void;
	};

	let {
		live,
		service,
		source = serviceSource(service),
		notes = [],
		onrange,
		onpick
	}: Props = $props();

	const LOAD_FAILED = "Couldn't load the chart history. Retrying when you change the range.";
	const PRESETS: Preset[] = ['1h', '24h', '7d', '30d'];
	const RELOAD_DEBOUNCE_MS = 400;
	const IGNORE_RANGE_MS = 600;

	let metric: Metric = $state('profit');
	let lane: LaneSel = $state('all');
	let preset: Preset = $state('1h');
	let res = $state<Resolution | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let latest = $state<number | null>(null);
	let container: HTMLDivElement | undefined = $state();

	const laneNumbers = $derived(
		Object.keys(live.lanes)
			.map(Number)
			.sort((a, b) => a - b)
	);
	const summary = $derived(
		`${metric === 'profit' ? 'Profit' : 'Balance'} for ${lane === 'all' ? 'all lanes' : `lane ${lane}`}, last ${preset}` +
			(latest === null
				? ''
				: `, latest ${metric === 'profit' ? formatProfit(latest) : latest.toFixed(8)}`)
	);

	let lib: typeof import('lightweight-charts') | null = null;
	let chart: IChartApi | null = null;
	let series: ISeriesApi<'Area'> | ISeriesApi<'Line'> | null = null;
	let markers: ISeriesMarkersPluginApi<Time> | null = null;
	let points: Point[] = [];
	let loaded: Range | null = null;
	let folder: LiveFolder | null = null;
	let request = 0;
	let ignoreRangeUntil = 0;
	let debounce: ReturnType<typeof setTimeout> | undefined;
	let shownSessions: Session[] = [];
	let step = 1;

	const nowSeconds = () => Math.floor(Date.now() / 1000);
	const iso = (seconds: number) => new Date(seconds * 1000).toISOString();
	const token = (name: string) =>
		getComputedStyle(document.documentElement).getPropertyValue(name).trim();
	const toChart = (p: Point) => ({ time: p.time as UTCTimestamp, value: p.value });
	const laneQuery = () => (lane === 'all' ? {} : { lane });

	function buildSeries() {
		if (!lib || !chart) return;
		if (series) chart.removeSeries(series);
		const color = token(metric === 'profit' ? '--color-profit' : '--color-accent');
		series =
			metric === 'profit'
				? chart.addSeries(lib.AreaSeries, {
						lineColor: color,
						topColor: `${color}55`,
						bottomColor: `${color}00`,
						lineWidth: 2
					})
				: chart.addSeries(lib.LineSeries, { color, lineWidth: 2 });
		markers = lib.createSeriesMarkers(series, []);
	}

	function draw(fit: boolean) {
		if (!series || !chart) return;
		ignoreRangeUntil = performance.now() + IGNORE_RANGE_MS;
		series.setData(points.map(toChart));
		if (fit) chart.timeScale().fitContent();
		latest = points.at(-1)?.value ?? null;
	}

	type Session = { lane: number; started_at: string | null; ended_at: string | null };

	function placeMarkers() {
		if (!markers) return;
		const color = token('--color-info');
		const snap = (at: string) => (Math.floor(Date.parse(at) / 1000 / step) * step) as UTCTimestamp;
		const list: SeriesMarker<Time>[] = [];
		for (const note of notes) {
			if (note.at)
				list.push({
					time: snap(note.at),
					position: 'aboveBar',
					shape: 'square',
					color: token('--color-warn'),
					text: 'Note'
				});
		}
		for (const s of shownSessions) {
			if (s.started_at)
				list.push({
					time: snap(s.started_at),
					position: 'belowBar',
					shape: 'arrowUp',
					color,
					text: `Lane ${s.lane} start`
				});
			if (s.ended_at)
				list.push({
					time: snap(s.ended_at),
					position: 'aboveBar',
					shape: 'arrowDown',
					color,
					text: `Lane ${s.lane} stop`
				});
		}
		markers.setMarkers(list.sort((a, b) => Number(a.time) - Number(b.time)));
	}

	async function load(range: Range, fit: boolean) {
		const id = ++request;
		loading = true;
		error = null;
		try {
			const [data, sessions] = await Promise.all([
				source.series({ metric, from: iso(range.from), to: iso(range.to), ...laneQuery() }),
				source.sessions({ since: iso(range.from), limit: 100, ...laneQuery() }).catch(() => null)
			]);
			if (id !== request) return;
			res = data.res;
			points = capPoints(toPoints(data.series, metric, lane));
			loaded = range;
			folder = new LiveFolder(metric, lane, data.res, points.at(-1) ?? null);
			draw(fit);
			shownSessions = sessions?.items ?? [];
			step = RES_SECONDS[data.res];
			placeMarkers();
			onrange?.({ from: iso(range.from), to: iso(range.to) });
		} catch {
			if (id !== request) return;
			error = LOAD_FAILED;
			folder ??= new LiveFolder(metric, lane, res ?? '1s', points.at(-1) ?? null);
		} finally {
			if (id === request) loading = false;
		}
	}

	function reload() {
		buildSeries();
		points = [];
		folder = null;
		void load(rangeFor(preset, nowSeconds()), true);
	}

	function onVisibleRange(range: LogicalRange | null) {
		if (!range || !chart || performance.now() < ignoreRangeUntil) return;
		clearTimeout(debounce);
		debounce = setTimeout(() => {
			const visible = chart?.timeScale().getVisibleRange();
			const logical = chart?.timeScale().getVisibleLogicalRange();
			if (!visible || !logical) return;
			const view = zoomedRange(
				logical.from,
				points[0]?.time ?? null,
				step,
				{ from: Number(visible.from), to: Number(visible.to) },
				nowSeconds()
			);
			if (needsReload(loaded, view)) void load(view, false);
		}, RELOAD_DEBOUNCE_MS);
	}

	function choose(update: () => void) {
		update();
		reload();
	}

	$effect(() => {
		void notes;
		placeMarkers();
	});

	onMount(() => {
		let stopCandles = () => {};
		let disposed = false;
		void (async () => {
			lib = await import('lightweight-charts');
			if (disposed || !container) return;
			chart = lib.createChart(container, {
				autoSize: true,
				layout: {
					background: { color: token('--color-surface') },
					textColor: token('--color-muted'),
					fontFamily: token('--font-sans')
				},
				grid: {
					vertLines: { color: token('--color-line') },
					horzLines: { color: token('--color-line') }
				},
				rightPriceScale: { borderColor: token('--color-line') },
				timeScale: { borderColor: token('--color-line'), timeVisible: true, secondsVisible: true }
			});
			chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRange);
			chart.subscribeClick((param) => {
				if (param.time !== undefined) onpick?.(iso(Number(param.time)));
			});
			stopCandles = live.onCandles((candles) => {
				if (!folder || !series) return;
				for (const candle of candles) {
					const point = folder.add(candle, live.lanes[candle.lane]?.session_id ?? null);
					if (!point) continue;
					series.update(toChart(point));
					points = capPoints(mergePoints(points, [point]));
					latest = point.value;
				}
			});
			reload();
		})();
		return () => {
			disposed = true;
			stopCandles();
			clearTimeout(debounce);
			chart?.remove();
			chart = null;
		};
	});
</script>

<div class="flex h-full min-h-72 flex-col gap-2">
	<div class="flex flex-wrap items-center gap-2 text-sm">
		<div class="flex rounded-md border border-line" role="group" aria-label="Metric">
			{#each ['profit', 'balance'] as const as m (m)}
				<button
					type="button"
					aria-pressed={metric === m}
					onclick={() => choose(() => (metric = m))}
					class="min-h-9 px-3 capitalize text-muted aria-pressed:bg-raised aria-pressed:text-ink"
					>{m}</button
				>
			{/each}
		</div>
		<div class="flex rounded-md border border-line" role="group" aria-label="Range">
			{#each PRESETS as p (p)}
				<button
					type="button"
					aria-pressed={preset === p}
					onclick={() => choose(() => (preset = p))}
					class="min-h-9 px-3 text-muted aria-pressed:bg-raised aria-pressed:text-ink">{p}</button
				>
			{/each}
		</div>
		<label class="flex items-center gap-2 text-muted">
			Lane
			<select
				value={String(lane)}
				onchange={(e) => {
					const value = e.currentTarget.value;
					choose(() => (lane = value === 'all' ? 'all' : Number(value)));
				}}
				class="min-h-9 rounded-md border border-line-strong bg-surface px-2 text-ink"
			>
				<option value="all">All lanes</option>
				{#each laneNumbers as n (n)}<option value={String(n)}>Lane {n}</option>{/each}
			</select>
		</label>
		{#if res}<span class="font-mono text-xs text-muted" title="Resolution">{res}</span>{/if}
	</div>
	<div class="relative min-h-60 flex-1">
		<div bind:this={container} class="absolute inset-0" role="figure" aria-label={summary}></div>
		{#if loading && !error}
			<p class="pointer-events-none absolute inset-x-0 top-2 text-center text-sm text-muted">
				Loading chart history
			</p>
		{/if}
		{#if error}
			<p role="alert" class="absolute inset-x-0 top-2 text-center text-sm text-warn">{error}</p>
		{/if}
	</div>
</div>
