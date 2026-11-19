<script lang="ts">
	import { addLine } from '#lib/control';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import { jobTopicFor, type JobLine } from '#lib/live/protocol';
	import Button from '#ui/Button';

	let dialog: HTMLDialogElement | undefined = $state();
	let lines: JobLine[] = $state.raw([]);
	let shownJob: string | null = null;

	const job = $derived(control.job);
	const done = $derived(job !== null && job.status !== 'running');

	$effect(() => {
		if (control.drawerOpen && dialog && !dialog.open) dialog.showModal();
		if (!control.drawerOpen && dialog?.open) dialog.close();
	});

	$effect(() => {
		const service = control.service;
		const current = job;
		if (!service || !current) return;
		if (shownJob !== current.job_id) {
			shownJob = current.job_id;
			lines = [];
		}
		const topic = jobTopicFor(service, current.job_id);
		return liveFor(service).onJob((message) => {
			if (message.topic !== topic) return;
			if (message.op === 'job') lines = addLine(lines, message);
			else void control.refreshJob();
		});
	});

	const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;
</script>

<dialog
	bind:this={dialog}
	onclose={() => control.closeJob()}
	aria-labelledby="job-title"
	class="m-0 ml-auto h-dvh max-h-none w-full max-w-none border-l border-line bg-surface p-0 text-ink backdrop:bg-canvas/60 md:w-[32rem]"
>
	<div class="flex h-full flex-col">
		<header class="flex items-center justify-between gap-3 border-b border-line p-4">
			<div>
				<h2 id="job-title" class="text-lg font-semibold">Job output</h2>
				{#if job}
					<p class="text-sm text-muted" aria-live="polite">
						{job.cmd}: {job.status}{#if done}
							, exit code {job.exit_code ?? 'none'} after {seconds(job.duration_ms)}{/if}
					</p>
				{/if}
			</div>
			<Button onclick={() => control.closeJob()}>Close</Button>
		</header>
		<ol class="flex-1 overflow-auto p-4 font-mono text-xs" aria-label="Output lines">
			{#each lines as line (line.seq)}
				<li class={line.stream === 'stderr' ? 'text-loss' : 'text-ink'}>{line.line}</li>
			{:else}
				<li class="text-muted">{done ? 'No output.' : 'Waiting for output...'}</li>
			{/each}
		</ol>
	</div>
</dialog>
