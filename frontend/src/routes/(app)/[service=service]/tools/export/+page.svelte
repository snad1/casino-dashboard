<script lang="ts">
	import { onDestroy } from 'svelte';
	import ActionButton from '#components/ActionButton';
	import { control } from '#lib/control.svelte';
	import { ToolJob } from '#lib/tool-job.svelte';
	import { downloadUrl } from '#lib/tools';

	let format: 'csv' | 'json' = $state('csv');
	let sessionId = $state('');
	let exported: 'csv' | 'json' = $state('csv');
	const job = new ToolJob();

	const ready = $derived(job.job?.status === 'succeeded');

	function start(event: SubmitEvent) {
		event.preventDefault();
		const body: Record<string, string | number> = { format };
		if (sessionId.trim()) body.session_id = Number(sessionId);
		exported = format;
		void job.start('export', body);
	}

	onDestroy(() => job.dispose());

	const input = 'min-h-11 rounded-md border border-line-strong bg-surface px-3';
</script>

<svelte:head><title>Export · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-3xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Export</h1>

	<form
		onsubmit={start}
		class="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface p-4"
	>
		<label class="flex flex-col gap-1 text-sm font-medium">
			Format
			<select class={input} bind:value={format}>
				<option value="csv">CSV</option>
				<option value="json">JSON</option>
			</select>
		</label>
		<label class="flex flex-col gap-1 text-sm font-medium">
			Session id {format === 'json' ? '(needed for JSON)' : '(blank for all)'}
			<input
				type="text"
				inputmode="numeric"
				class="{input} w-36"
				bind:value={sessionId}
				required={format === 'json'}
			/>
		</label>
		<ActionButton name="export" busy={job.running} submit />
	</form>

	{#if job.job}
		<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="file-title">
			<h2 id="file-title" class="font-semibold">File</h2>
			{#if ready && control.service}
				<p class="mt-1 text-sm text-muted">Ready for an hour.</p>
				<!-- eslint-disable svelte/no-navigation-without-resolve -- an api download, not an app route -->
				<a
					class="mt-3 inline-flex min-h-11 items-center rounded-md bg-accent px-4 text-sm font-medium text-on-accent"
					href={downloadUrl(control.service, job.job.job_id, exported)}
					download>Download</a
				>
				<!-- eslint-enable svelte/no-navigation-without-resolve -->
			{:else}
				<p class="mt-1 text-sm text-muted" aria-live="polite">
					{job.job.status === 'running'
						? 'Writing the file...'
						: `The export ${job.job.status}. Try again.`}
				</p>
			{/if}
		</section>
	{/if}
</div>
