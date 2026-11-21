<script lang="ts">
	import { onDestroy } from 'svelte';
	import ActionButton from '#components/ActionButton';
	import JobOutput from '#components/JobOutput';
	import { control } from '#lib/control.svelte';
	import { ToolJob } from '#lib/tool-job.svelte';

	let report: string | null = $state(null);
	let diagnosing = $state(false);
	let version: string | null = $state(null);
	let reading = $state(false);
	let count = $state(20);
	let lanes = $state('');
	const job = new ToolJob();

	async function diagnose() {
		diagnosing = true;
		const out = await control.run('diagnose');
		diagnosing = false;
		if (out) report = out.output ?? '';
	}

	async function readVersion() {
		reading = true;
		const out = await control.run('version');
		reading = false;
		if (out) version = out.output ?? '';
	}

	function benchmark() {
		const body: Record<string, number> = { count };
		if (lanes.trim()) body.lanes = Number(lanes);
		void job.start('benchmark', body);
	}

	onDestroy(() => job.dispose());

	const section = 'rounded-lg border border-line bg-surface p-4';
	const input = 'min-h-11 w-28 rounded-md border border-line-strong bg-surface px-3';
</script>

<svelte:head><title>Health · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Health</h1>

	<section class={section} aria-labelledby="diagnose-title">
		<h2 id="diagnose-title" class="font-semibold">Diagnose</h2>
		<p class="mb-3 text-sm text-muted">What limits the running session's speed. Places no bets.</p>
		<ActionButton
			name="diagnose"
			busy={diagnosing}
			label={diagnosing ? 'Diagnosing...' : 'Diagnose'}
			onpress={() => void diagnose()}
		/>
		{#if report !== null}
			<pre
				class="mt-3 max-h-96 overflow-auto rounded-md bg-raised p-3 font-mono text-xs whitespace-pre-wrap"
				aria-label="Diagnose report">{report || 'No output.'}</pre>
		{/if}
	</section>

	<section class={section} aria-labelledby="checks-title">
		<h2 id="checks-title" class="font-semibold">Doctor and benchmark</h2>
		<p class="mb-3 text-sm text-muted">
			Doctor checks the host and lists fixes. Benchmark times api calls and places no bets.
		</p>
		<div class="flex flex-wrap items-end gap-3">
			<ActionButton name="doctor" busy={job.running} onpress={() => void job.start('doctor')} />
			<label class="flex flex-col gap-1 text-sm font-medium">
				Calls
				<input type="number" min="3" max="200" class={input} bind:value={count} />
			</label>
			<label class="flex flex-col gap-1 text-sm font-medium">
				Lanes (blank for one)
				<input type="text" inputmode="numeric" class={input} bind:value={lanes} />
			</label>
			<ActionButton name="benchmark" busy={job.running} onpress={benchmark} />
		</div>
		{#if job.job}
			<p class="mt-3 text-sm text-muted" aria-live="polite">
				{job.job.cmd}: {job.job.status}{#if job.job.status !== 'running'}, exit code {job.job
						.exit_code ?? 'none'}{/if}
			</p>
			<JobOutput
				lines={job.lines}
				done={job.job.status !== 'running'}
				class="mt-2 max-h-96 rounded-md bg-raised p-3"
			/>
		{/if}
	</section>

	<section class={section} aria-labelledby="version-title">
		<h2 id="version-title" class="font-semibold">Version</h2>
		<p class="mb-3 text-sm text-muted">The bot and wolfctl versions on the host.</p>
		<ActionButton name="version" busy={reading} onpress={() => void readVersion()} />
		{#if version !== null}
			<pre
				class="mt-3 rounded-md bg-raised p-3 font-mono text-xs whitespace-pre-wrap"
				aria-label="Version report">{version || 'No output.'}</pre>
		{/if}
	</section>

	<section class="{section} border-loss" aria-labelledby="fix-title">
		<h2 id="fix-title" class="font-semibold text-loss">Doctor fix</h2>
		<p class="mb-3 text-sm text-muted">
			Applies doctor's safe fixes. Needs an admin, a fresh code and the service name typed out.
		</p>
		<ActionButton name="doctor_fix" danger />
	</section>
</div>
