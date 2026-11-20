<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ActionButton from '#components/ActionButton';
	import type { ActionBody } from '#lib/control';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import Notice from '#ui/Notice';

	const service = $derived(page.params.service ?? '');
	const live = $derived(liveFor(service));
	const lanes = $derived(
		Object.keys(live.lanes)
			.map(Number)
			.sort((a, b) => a - b)
	);
	let days = $state(7);
	let branch = $state('');

	const updateBody = $derived.by(() => {
		const body: ActionBody = {};
		if (branch.trim()) body.branch = branch.trim();
		return body;
	});

	const section = 'rounded-lg border border-line bg-surface p-4';
	const row = 'flex flex-wrap items-start gap-3';
	const input =
		'min-h-11 rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';
</script>

<svelte:head><title>Control · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Control</h1>

	{#if control.loadError}
		<Notice tone="error">{control.loadError}</Notice>
	{:else if !control.menu}
		<p class="text-sm text-muted" role="status">Loading controls...</p>
	{:else}
		{#if control.menu.running_job}
			<Notice>
				{control.menu.running_job.cmd} is running.
				<button type="button" class="underline" onclick={() => (control.drawerOpen = true)}
					>Show output</button
				>
			</Notice>
		{/if}

		<section class={section} aria-labelledby="lanes-title">
			<h2 id="lanes-title" class="mb-3 font-semibold">Lanes</h2>
			<ul class="flex flex-col gap-3">
				<li class={row}>
					<span class="min-w-20 pt-3 text-sm font-medium">All lanes</span>
					<ActionButton name="pause" body={{ target: 'all' }} />
					<ActionButton name="resume" body={{ target: 'all' }} />
					<ActionButton name="reload" body={{ target: 'all' }} />
				</li>
				{#each lanes as lane (lane)}
					<li class={row}>
						<span class="min-w-20 pt-3 text-sm">
							Lane {lane}
							<span class="block text-xs text-muted">{live.lanes[lane]?.status ?? 'unknown'}</span>
						</span>
						<ActionButton name="pause" body={{ target: lane }} />
						<ActionButton name="resume" body={{ target: lane }} />
						<ActionButton name="reload" body={{ target: lane }} />
					</li>
				{/each}
			</ul>
		</section>

		<section class={section} aria-labelledby="count-title">
			<h2 id="count-title" class="mb-2 font-semibold">Lanes count</h2>
			<p class="text-sm">
				<a class="underline" href={resolve('/(app)/[service=service]/lanes', { service })}
					>Change lanes and split</a
				>
				<span class="text-muted">with a preview of each lane's share and what restarts.</span>
			</p>
		</section>

		<section class={section} aria-labelledby="service-title">
			<h2 id="service-title" class="mb-3 font-semibold">Service</h2>
			<div class={row}>
				<ActionButton name="start" />
				<ActionButton name="stop" />
				<ActionButton name="restart" />
			</div>
		</section>

		<section class="{section} border-loss" aria-labelledby="danger-title">
			<h2 id="danger-title" class="font-semibold text-loss">Danger zone</h2>
			<p class="mb-3 text-sm text-muted">
				These need an admin, a fresh authenticator code and the service name typed out.
			</p>
			<div class="flex flex-col gap-4">
				<div class={row}>
					<ActionButton name="run_reset" danger />
					<ActionButton name="reset" danger />
					<ActionButton name="doctor_fix" danger />
					<ActionButton name="stop_all" danger />
				</div>
				<div class={row}>
					<label class="flex flex-col gap-1 text-sm font-medium">
						Older than (days)
						<input type="number" min="1" max="30" class={input} bind:value={days} />
					</label>
					<div class="pt-6"><ActionButton name="cleanup" body={{ days }} danger /></div>
				</div>
				<div class={row}>
					<label class="flex flex-col gap-1 text-sm font-medium">
						Branch (blank keeps the current one)
						<input type="text" class={input} bind:value={branch} autocomplete="off" />
					</label>
					<div class="pt-6"><ActionButton name="update" body={updateBody} danger /></div>
				</div>
			</div>
		</section>
	{/if}
</div>
