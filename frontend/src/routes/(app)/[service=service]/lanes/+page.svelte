<script lang="ts">
	import { page } from '$app/state';
	import ActionButton from '#components/ActionButton';
	import ConfigDiff from '#components/ConfigDiff';
	import { ApiError, unwrap } from '#lib/api/client';
	import { display, type ConfigChange, type ConfigOut, type Preview } from '#lib/config';
	import { control } from '#lib/control.svelte';
	import { formatAmount } from '#lib/format';
	import {
		LANE_STATUS_WORDS,
		laneNameError,
		lanePlan,
		laneShares,
		laneTitle,
		parseArchives,
		type Split
	} from '#lib/lanes';
	import { liveFor } from '#lib/live/index';
	import { topicFor } from '#lib/live/protocol';
	import { can, denialReason } from '#lib/permissions';
	import { api, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';

	const LANES_MAX = 10;

	const service = $derived(page.params.service ?? '');
	const live = $derived(liveFor(service));

	let config = $state<ConfigOut | null>(null);
	let loadError = $state<string | null>(null);
	let count = $state(1);
	let split = $state<Split>('none');
	let preview = $state<Preview | null>(null);
	let previewFor = $state('');
	let previewError = $state<string | null>(null);
	let previewing = $state(false);
	let archivesText = $state<string | null>(null);
	let loadingArchives = $state(false);
	let handledJob: string | null = null;
	let newName = $state('');
	let copyFrom = $state('');
	let renames = $state<Record<number, string>>({});

	const changes = $derived.by(() => {
		const out: ConfigChange[] = [];
		if (!config) return out;
		if (count !== config.lane_count) out.push({ key: 'lanes', value: String(count), lane: null });
		if (split !== config.lane_split) out.push({ key: 'lane_split', value: split, lane: null });
		return out;
	});
	const changesKey = $derived(JSON.stringify(changes));
	const previewFresh = $derived(preview !== null && previewFor === changesKey);
	const balance = $derived(
		Object.values(live.lanes).find((lane) => typeof lane.balance === 'number')?.balance ?? null
	);
	const shares = $derived(config ? laneShares(config, count, split, balance) : []);
	const plan = $derived(config ? lanePlan(live.lanes, config.lane_count, count) : null);
	const archives = $derived(archivesText === null ? null : parseArchives(archivesText));
	const configReason = $derived(denialReason(session.me, 'config.write', service));
	const laneRows = $derived(
		Object.entries(live.lanes)
			.map(([n, lane]) => ({ n: Number(n), lane }))
			.sort((a, b) => a.n - b.n)
	);
	const newNameError = $derived(newName === '' ? null : laneNameError(newName));
	const addBody = $derived(
		copyFrom === '' ? { name: newName } : { name: newName, copy_from: Number(copyFrom) }
	);
	const renameTo = (n: number, current: string | null | undefined) => renames[n] ?? current ?? '';

	async function load() {
		try {
			const loaded = await unwrap(api.GET('/{service}/config', { params: { path: { service } } }));
			config = loaded;
			count = loaded.lane_count;
			split = (loaded.lane_split as Split) ?? 'none';
			loadError = null;
		} catch (e) {
			loadError = e instanceof ApiError ? e.detail : String(e);
		}
	}

	async function loadArchives() {
		loadingArchives = true;
		const out = await control.run('archives');
		loadingArchives = false;
		if (out) archivesText = out.output ?? '';
	}

	$effect(() => {
		if (service && can(session.me, 'config.write', service)) void load();
	});

	$effect(() => {
		if (control.menu && can(session.me, 'control.basic', service)) void loadArchives();
	});

	$effect(() => {
		const job = control.job;
		if (!job || job.cmd !== `${service}.lanes` || job.status === 'running') return;
		if (handledJob === job.job_id) return;
		handledJob = job.job_id;
		live.resubscribe(topicFor(service));
		preview = null;
		void load();
	});

	/** Restart one lane so it runs on the saved settings, then show what is still waiting. */
	async function applyPending(lane: number) {
		const out = await control.ask('lane_restart', { lane });
		if (out?.status === 'succeeded') await load();
	}

	async function runPreview() {
		previewing = true;
		previewError = null;
		const sent = changesKey;
		try {
			preview = await unwrap(
				api.POST('/{service}/config/preview', { params: { path: { service } }, body: { changes } })
			);
			previewFor = sent;
		} catch (e) {
			preview = null;
			previewError = e instanceof ApiError ? e.detail : String(e);
		} finally {
			previewing = false;
		}
	}

	const amount = (value: number | null) => (value === null ? 'unknown' : formatAmount(value));
	const list = (lanes: number[]) => lanes.map((l) => `lane ${l}`).join(', ');
	const section = 'rounded-lg border border-line bg-surface p-4';
	const input =
		'min-h-11 rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';
</script>

<svelte:head><title>Lanes · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Lanes</h1>

	<section class={section} aria-labelledby="count-title">
		<h2 id="count-title" class="mb-3 font-semibold">Lanes and split</h2>
		{#if configReason}
			<p class="text-sm text-muted">{configReason}</p>
		{:else if loadError}
			<Notice tone="error">{loadError}</Notice>
		{:else if !config}
			<p class="text-sm text-muted" role="status">Loading lanes...</p>
		{:else}
			<p class="mb-3 text-sm text-muted">
				Now {config.lane_count} lane{config.lane_count === 1 ? '' : 's'}, split {config.lane_split}.
			</p>
			<div class="flex flex-wrap items-end gap-3">
				<label class="flex flex-col gap-1 text-sm font-medium">
					Lanes
					<input type="number" min="1" max={LANES_MAX} class="{input} w-28" bind:value={count} />
				</label>
				<label class="flex flex-col gap-1 text-sm font-medium">
					Split
					<select class={input} bind:value={split}>
						{#each config.split_choices as choice (choice)}
							<option value={choice}>{choice}</option>
						{/each}
					</select>
				</label>
				<Button
					variant="primary"
					disabled={changes.length === 0 || previewing}
					onclick={() => void runPreview()}>{previewing ? 'Checking...' : 'Preview'}</Button
				>
				{#if previewFresh}
					<ActionButton name="lanes" body={{ count, split }} label="Change lanes" />
				{:else}
					<span class="pb-3 text-sm text-muted" aria-live="polite">
						{changes.length === 0 ? 'No changes.' : 'Preview the change before applying it.'}
					</span>
				{/if}
			</div>

			{#if previewError}
				<div class="mt-3"><Notice tone="error">{previewError}</Notice></div>
			{/if}

			{#if preview && previewFresh}
				<div class="mt-4 flex flex-col gap-4">
					<ConfigDiff {preview} />
					{#if plan}
						<Notice>
							Running lanes keep betting.
							{#if plan.start}
								<span class="block"
									>{plan.start} new lane{plan.start === 1 ? '' : 's'} start{plan.start === 1
										? 's'
										: ''}.</span
								>
							{/if}
							{#if plan.stop.length}
								<span class="block">{list(plan.stop)} stop{plan.stop.length === 1 ? 's' : ''}.</span
								>
							{/if}
						</Notice>
					{/if}
					<table class="w-full text-left text-sm" aria-label="Lane shares">
						<thead class="text-muted">
							<tr><th>Lane</th><th>Base bet</th><th>May risk</th></tr>
						</thead>
						<tbody>
							{#each shares as share (share.lane)}
								<tr class="border-t border-line">
									<td class="py-2">Lane {share.lane}</td>
									<td class="font-mono"
										>{amount(share.baseBet)}{share.ownBet ? ' (its own setting)' : ''}</td
									>
									<td class="font-mono">{amount(share.budget)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{/if}
	</section>

	<section class={section} aria-labelledby="each-title">
		<h2 id="each-title" class="mb-3 font-semibold">Each lane</h2>
		{#if configReason}
			<p class="text-sm text-muted">{configReason}</p>
		{:else}
			<form
				class="mb-4 flex flex-wrap items-end gap-3"
				aria-labelledby="add-title"
				onsubmit={(e) => e.preventDefault()}
			>
				<h3 id="add-title" class="w-full text-sm font-semibold">Add a lane</h3>
				<label class="flex flex-col gap-1 text-sm font-medium">
					Name
					<input
						class="{input} w-48"
						maxlength="32"
						autocomplete="off"
						aria-invalid={newNameError !== null}
						aria-describedby="new-name-help"
						bind:value={newName}
					/>
				</label>
				<label class="flex flex-col gap-1 text-sm font-medium">
					Copy settings from
					<select class={input} bind:value={copyFrom}>
						<option value="">Base config</option>
						{#each laneRows as row (row.n)}
							{#if row.n > 1}
								<option value={String(row.n)}>{laneTitle(row.n, row.lane.name)}</option>
							{/if}
						{/each}
					</select>
				</label>
				{#if newName !== '' && newNameError === null}
					<ActionButton name="lane_add" body={addBody} label="Add lane" />
				{:else}
					<Button variant="secondary" disabled>Add lane</Button>
				{/if}
				<p id="new-name-help" class="w-full text-xs {newNameError ? 'text-loss' : 'text-muted'}">
					{newNameError ??
						'The new lane starts betting with its own number. It shows here once it reports.'}
				</p>
			</form>

			{#if laneRows.length === 0}
				<p class="text-sm text-muted">No lane has reported yet.</p>
			{:else}
				<ul class="flex flex-col divide-y divide-line" aria-label="Lane controls">
					{#each laneRows as row (row.n)}
						{@const value = renameTo(row.n, row.lane.name)}
						{@const error = laneNameError(value)}
						{@const pending = config?.pending?.[String(row.n)] ?? []}
						<li
							class="flex flex-wrap items-end gap-3 py-3"
							aria-label={laneTitle(row.n, row.lane.name)}
						>
							<div class="flex min-w-40 flex-col">
								<span class="font-medium break-all">{laneTitle(row.n, row.lane.name)}</span>
								<span class="text-xs text-muted"
									>{LANE_STATUS_WORDS[row.lane.status ?? 'stale']}</span
								>
							</div>
							<label class="flex flex-col gap-1 text-sm font-medium">
								New name
								<input
									class="{input} w-44"
									maxlength="32"
									autocomplete="off"
									aria-invalid={value !== '' && error !== null}
									{value}
									oninput={(e) => (renames[row.n] = e.currentTarget.value)}
								/>
							</label>
							{#if error === null && value !== (row.lane.name ?? '')}
								<ActionButton
									name="lane_rename"
									body={{ lane: row.n, name: value }}
									label="Rename"
									compact
								/>
							{:else}
								<Button variant="secondary" disabled>Rename</Button>
							{/if}
							<ActionButton name="lane_restart" body={{ lane: row.n }} label="Restart" compact />
							{#if row.n > 1}
								<ActionButton
									name="lane_remove"
									body={{ lane: row.n }}
									label="Remove"
									danger
									compact
								/>
							{/if}
							{#if value !== '' && error}
								<p class="w-full text-xs text-loss">{error}</p>
							{/if}
							{#if pending.length}
								<div class="flex w-full flex-wrap items-center gap-3">
									<p class="text-sm text-warn">
										Waiting for a restart: {pending
											.map((p) => `${p.key} ${display(p.running)} -> ${display(p.saved)}`)
											.join(', ')}
									</p>
									<Button onclick={() => void applyPending(row.n)}>Apply</Button>
								</div>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</section>

	<section class={section} aria-labelledby="archives-title">
		<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
			<h2 id="archives-title" class="font-semibold">Archives</h2>
			<ActionButton
				name="archives"
				busy={loadingArchives}
				label={loadingArchives ? 'Loading...' : 'Refresh'}
				onpress={() => void loadArchives()}
			/>
		</div>
		{#if archives === null}
			<p class="text-sm text-muted">Each statistics reset archives the database here.</p>
		{:else if archives.rows.length === 0}
			<p class="text-sm text-muted">No archives yet.</p>
		{:else}
			<table class="w-full text-left text-sm" aria-label="Archived databases">
				<thead class="text-muted">
					<tr><th>File</th><th>Sessions</th><th>Span</th><th>Size</th></tr>
				</thead>
				<tbody>
					{#each archives.rows as row (row.name)}
						<tr class="border-t border-line">
							<td class="py-2 font-mono text-xs break-all">{row.name}</td>
							<td>{row.sessions}</td>
							<td class="text-xs">{row.span}</td>
							<td>{row.size}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if archives.total}<p class="mt-2 text-sm text-muted">Total {archives.total}</p>{/if}
		{/if}
	</section>

	<section class="{section} border-loss" aria-labelledby="reset-title">
		<h2 id="reset-title" class="font-semibold text-loss">Resets</h2>
		<p class="mb-3 text-sm text-muted">
			Run reset starts the run profit total again on all lanes at once (the bot has no per-lane run
			reset) and restarts the running lanes. Reset statistics archives the database. Both need an
			admin, a fresh authenticator code and the service name typed out.
		</p>
		<div class="flex flex-wrap gap-3">
			<ActionButton name="run_reset" danger />
			<ActionButton name="reset" danger />
		</div>
	</section>
</div>
