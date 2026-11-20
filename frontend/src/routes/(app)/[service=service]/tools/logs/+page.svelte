<script lang="ts">
	import { onDestroy } from 'svelte';
	import ActionButton from '#components/ActionButton';
	import { control } from '#lib/control.svelte';
	import { ToolJob } from '#lib/tool-job.svelte';
	import {
		capLines,
		filterLines,
		LEVELS,
		MAX_LOG_LINES,
		parseLogLine,
		type Level,
		type LogLine
	} from '#lib/tools';
	import Button from '#ui/Button';
	import VirtualList from '#ui/VirtualList';

	const UNITS = [
		{ value: 'cli', label: 'Bot' },
		{ value: 'tg', label: 'Telegram' },
		{ value: 'web', label: 'Web' }
	] as const;
	const ROW = 22;

	let unit: (typeof UNITS)[number]['value'] = $state('cli');
	let count = $state(500);
	let level: Level | 'all' = $state('all');
	let lane: number | 'all' = $state('all');
	let search = $state('');
	let lines: LogLine[] = $state.raw([]);
	let loading = $state(false);
	let nextId = 0;
	let lastSeq = -1;
	const follow = new ToolJob(true);

	const shown = $derived(filterLines(lines, { level, lane, search }));
	const lanes = $derived(
		[...new Set(lines.map((l) => l.lane).filter((l): l is number => l !== null))].sort(
			(a, b) => a - b
		)
	);

	function add(raw: string[]) {
		const parsed = raw.map((text) => parseLogLine(nextId++, text));
		lines = capLines(lines, parsed);
	}

	async function load() {
		loading = true;
		const out = await control.run('logs', { service: unit, lines: count });
		loading = false;
		if (out?.output === null || out?.output === undefined) return;
		lines = [];
		add(out.output.split('\n').filter((line) => line !== ''));
	}

	async function startFollow() {
		lastSeq = -1;
		await follow.start('logs_follow', { service: unit });
	}

	$effect(() => {
		const fresh = follow.lines.filter((line) => line.seq > lastSeq);
		if (!fresh.length) return;
		lastSeq = fresh[fresh.length - 1]?.seq ?? lastSeq;
		add(fresh.map((line) => line.line));
	});

	function leave() {
		follow.dispose();
	}

	onDestroy(leave);
</script>

<svelte:head><title>Logs · Casino dashboard</title></svelte:head>
<svelte:window onpagehide={leave} />

<div class="mx-auto flex max-w-5xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Logs</h1>

	<div class="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface p-4">
		<label class="flex flex-col gap-1 text-sm font-medium">
			Service
			<select
				class="min-h-11 rounded-md border border-line-strong bg-surface px-3"
				bind:value={unit}
			>
				{#each UNITS as option (option.value)}<option value={option.value}>{option.label}</option
					>{/each}
			</select>
		</label>
		<label class="flex flex-col gap-1 text-sm font-medium">
			Lines
			<input
				type="number"
				min="1"
				max="2000"
				class="min-h-11 w-28 rounded-md border border-line-strong bg-surface px-3"
				bind:value={count}
			/>
		</label>
		<ActionButton
			name="logs"
			busy={loading}
			label={loading ? 'Loading...' : 'Load'}
			onpress={() => void load()}
		/>
		{#if follow.running}
			<Button variant="danger" onclick={() => void follow.cancel()}>Stop</Button>
			<span class="pb-3 text-sm text-muted" role="status">Following new lines...</span>
		{:else}
			<ActionButton name="logs_follow" busy={false} onpress={() => void startFollow()} />
		{/if}
	</div>

	<div class="flex flex-wrap items-end gap-3">
		<label class="flex flex-col gap-1 text-sm font-medium">
			Level
			<select
				class="min-h-11 rounded-md border border-line-strong bg-surface px-3"
				bind:value={level}
			>
				<option value="all">All levels</option>
				{#each LEVELS as option (option)}<option value={option}>{option}</option>{/each}
			</select>
		</label>
		<label class="flex flex-col gap-1 text-sm font-medium">
			Lane
			<select
				class="min-h-11 rounded-md border border-line-strong bg-surface px-3"
				bind:value={lane}
			>
				<option value="all">All lanes</option>
				{#each lanes as option (option)}<option value={option}>Lane {option}</option>{/each}
			</select>
		</label>
		<label class="flex flex-1 flex-col gap-1 text-sm font-medium">
			Search
			<input
				type="search"
				class="min-h-11 rounded-md border border-line-strong bg-surface px-3"
				bind:value={search}
			/>
		</label>
	</div>

	<p class="text-sm text-muted" aria-live="polite">
		{shown.length} of {lines.length} lines (the newest {MAX_LOG_LINES} are kept)
	</p>
	<div class="h-[60dvh] rounded-lg border border-line bg-surface p-2">
		<VirtualList items={shown} key={(l) => l.id} rowHeight={ROW} label="Log lines">
			{#snippet row(line: LogLine)}
				<div
					role="row"
					class="truncate font-mono text-xs leading-[22px] {line.level === 'ERROR' ||
					line.level === 'CRITICAL'
						? 'text-loss'
						: line.level === 'WARNING'
							? 'text-warn'
							: 'text-ink'}"
					title={line.text}
				>
					<span role="cell">{line.text}</span>
				</div>
			{/snippet}
		</VirtualList>
	</div>
</div>
