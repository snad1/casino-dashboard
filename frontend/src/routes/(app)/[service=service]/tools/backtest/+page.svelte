<script lang="ts">
	import { onDestroy } from 'svelte';
	import ActionButton from '#components/ActionButton';
	import JobOutput from '#components/JobOutput';
	import { control } from '#lib/control.svelte';
	import { ToolJob } from '#lib/tool-job.svelte';
	import { parseBacktest, parseProgress, resultRows } from '#lib/tools';

	let source: 'simulate' | 'replay' = $state('simulate');
	let bets = $state(10000);
	let sessionId = $state('');
	let seed = $state('');
	let sessions = $state('');
	let runs = $state('');
	let balance = $state('');
	let preset = $state('');
	const job = new ToolJob();

	const progress = $derived.by(() => {
		if (job.job?.status === 'succeeded') return 1;
		for (let i = job.lines.length - 1; i >= 0; i--) {
			const line = job.lines[i];
			if (line?.stream !== 'stderr') continue;
			const value = parseProgress(line.line);
			if (value !== null) return value;
		}
		return 0;
	});
	const result = $derived(
		parseBacktest(job.lines.filter((l) => l.stream === 'stdout').map((l) => l.line))
	);
	const outputLines = $derived(job.lines.filter((l) => parseProgress(l.line) === null));

	function run(event: SubmitEvent) {
		event.preventDefault();
		const body: Record<string, number | string> = {};
		if (source === 'simulate') body.simulate = bets;
		else body.replay_session = Number(sessionId);
		const numbers = { seed, sessions, runs, balance } as const;
		for (const [key, value] of Object.entries(numbers)) {
			if (value.trim() && control.takes('backtest', key)) body[key] = Number(value);
		}
		if (preset.trim()) body.preset = preset.trim();
		void job.start('backtest', body);
	}

	onDestroy(() => job.dispose());

	const input = 'min-h-11 w-36 rounded-md border border-line-strong bg-surface px-3';
</script>

<svelte:head><title>Backtest · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Backtest</h1>
	<p class="text-sm text-muted">Runs offline: no api calls and no real balance.</p>

	<form onsubmit={run} class="flex flex-col gap-4 rounded-lg border border-line bg-surface p-4">
		<fieldset class="flex flex-wrap gap-4">
			<legend class="mb-2 text-sm font-medium">Source</legend>
			<label class="flex min-h-11 items-center gap-2 text-sm">
				<input type="radio" value="simulate" bind:group={source} /> Simulate bets
			</label>
			<label class="flex min-h-11 items-center gap-2 text-sm">
				<input type="radio" value="replay" bind:group={source} /> Replay a session
			</label>
		</fieldset>
		<div class="flex flex-wrap items-end gap-3">
			{#if source === 'simulate'}
				<label class="flex flex-col gap-1 text-sm font-medium">
					Bets
					<input type="number" min="1" max="5000000" class={input} bind:value={bets} />
				</label>
			{:else}
				<label class="flex flex-col gap-1 text-sm font-medium">
					Session id
					<input type="text" inputmode="numeric" class={input} bind:value={sessionId} required />
				</label>
			{/if}
			<label class="flex flex-col gap-1 text-sm font-medium">
				Seed
				<input type="text" inputmode="numeric" class={input} bind:value={seed} />
			</label>
			{#if control.takes('backtest', 'sessions')}
				<label class="flex flex-col gap-1 text-sm font-medium">
					Sessions
					<input type="text" inputmode="numeric" class={input} bind:value={sessions} />
				</label>
			{/if}
			{#if control.takes('backtest', 'runs')}
				<label class="flex flex-col gap-1 text-sm font-medium">
					Runs
					<input type="text" inputmode="numeric" class={input} bind:value={runs} />
				</label>
			{/if}
			{#if control.takes('backtest', 'balance')}
				<label class="flex flex-col gap-1 text-sm font-medium">
					Balance
					<input type="text" inputmode="decimal" class={input} bind:value={balance} />
				</label>
			{/if}
			<label class="flex flex-col gap-1 text-sm font-medium">
				Preset
				<input type="text" class={input} bind:value={preset} autocomplete="off" />
			</label>
		</div>
		<div>
			<ActionButton name="backtest" busy={job.running} submit />
		</div>
	</form>

	{#if job.job}
		<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="run-title">
			<h2 id="run-title" class="font-semibold">Run</h2>
			<p class="mt-1 text-sm text-muted" aria-live="polite">
				{job.job.status}{#if job.job.status !== 'running'}, exit code {job.job.exit_code ??
						'none'}{/if}
			</p>
			<progress class="mt-2 h-2 w-full" max="1" value={progress} aria-label="Backtest progress"
				>{Math.round(progress * 100)}%</progress
			>
			{#if result}
				<table class="mt-4 w-full text-left text-sm" aria-label="Backtest result">
					<tbody>
						{#each resultRows(result) as [key, value] (key)}
							<tr class="border-t border-line">
								<th scope="row" class="py-2 font-medium">{key}</th>
								<td class="font-mono">{value}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<JobOutput
					lines={outputLines}
					done={job.job.status !== 'running'}
					class="mt-3 max-h-64 rounded-md bg-raised p-3"
				/>
			{/if}
		</section>
	{/if}
</div>
