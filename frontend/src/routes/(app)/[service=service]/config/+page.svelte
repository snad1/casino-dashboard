<script lang="ts">
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import { ApiError, unwrap } from '#lib/api/client';
	import {
		appliedSince,
		badgeClass,
		BASE,
		changesFrom,
		display,
		fieldKind,
		hasLiveChange,
		initialEdits,
		LANE_SPLIT,
		needsRestart,
		parsePasted,
		RELOAD_WAIT_MS,
		reloadMarks,
		type ConfigChange,
		type ConfigKey,
		type ConfigOut,
		type Edits,
		type Pasted,
		type Preview,
		type Scope
	} from '#lib/config';
	import ConfigDiff from '#components/ConfigDiff';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import { can } from '#lib/permissions';
	import { api, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';

	const service = $derived(page.params.service ?? '');
	const live = $derived(liveFor(service));

	let config = $state<ConfigOut | null>(null);
	let loadError = $state<string | null>(null);
	let edits = $state<Edits>({});
	let scope = $state<Scope>(BASE);
	let preview = $state<Preview | null>(null);
	let previewFor = $state('');
	let previewError = $state<string | null>(null);
	let previewing = $state(false);
	let saving = $state(false);
	let marks = $state<Record<number, number | null> | null>(null);
	let waitedOut = $state(false);
	let restartOnly = $state(false);
	let waitTimer: ReturnType<typeof setTimeout> | null = null;
	let pasteText = $state('');
	let pasted = $state<{ filled: number; skipped: Pasted['skipped'] } | null>(null);

	const scopes = $derived(config ? [BASE, ...laneScopes(config)] : [BASE]);
	const keys = $derived(
		config ? config.keys.filter((k) => scope === BASE || k.lane_overridable) : []
	);
	const changes: ConfigChange[] = $derived(config ? changesFrom(config, edits) : []);
	const changesKey = $derived(JSON.stringify(changes));
	const previewFresh = $derived(preview !== null && previewFor === changesKey);
	const applied = $derived(marks ? appliedSince(live.lanes, marks) : []);

	async function load() {
		try {
			const loaded = await unwrap(api.GET('/{service}/config', { params: { path: { service } } }));
			edits = Object.fromEntries(
				[BASE, ...laneScopes(loaded)].map((s) => [s, initialEdits(loaded, s)])
			);
			config = loaded;
			loadError = null;
		} catch (e) {
			loadError = e instanceof ApiError ? e.detail : String(e);
		}
	}

	function laneScopes(loaded: ConfigOut): Scope[] {
		const last = Math.min(loaded.lane_count, loaded.override_lanes.max);
		return Array.from({ length: Math.max(0, last - loaded.override_lanes.min + 1) }, (_, i) =>
			String(i + loaded.override_lanes.min)
		);
	}

	$effect(() => {
		if (service && can(session.me, 'config.write', service)) void load();
	});

	async function runPreview() {
		previewing = true;
		previewError = null;
		const sent = changesKey;
		try {
			preview = await unwrap(
				api.POST('/{service}/config/preview', {
					params: { path: { service } },
					body: { changes }
				})
			);
			previewFor = sent;
		} catch (e) {
			preview = null;
			previewError = e instanceof ApiError ? e.detail : String(e);
		} finally {
			previewing = false;
		}
	}

	async function save(thenRestart: boolean) {
		if (!preview || !previewFresh) return;
		const shown = preview;
		saving = true;
		const before = reloadMarks(live.lanes);
		const out = await control.ask('config_set', { changes });
		saving = false;
		if (!out || out.status !== 'succeeded') return;
		waitForReload(before, !hasLiveChange(shown));
		preview = null;
		await load();
		if (thenRestart) await control.ask('restart');
	}

	function waitForReload(before: Record<number, number | null>, onlyRestart: boolean) {
		if (waitTimer) clearTimeout(waitTimer);
		waitedOut = false;
		restartOnly = onlyRestart;
		marks = onlyRestart ? null : before;
		if (!onlyRestart) waitTimer = setTimeout(() => (waitedOut = true), RELOAD_WAIT_MS);
	}

	/** Put a pasted config into the Base form; nothing is sent until Preview. */
	function fillFromPaste() {
		if (!config) return;
		const out = parsePasted(pasteText, config.keys);
		const base = edits[BASE] ?? {};
		let filled = 0;
		for (const [key, value] of Object.entries(out.values)) {
			const edit = base[key];
			if (!edit) continue;
			edit.value = value;
			filled++;
		}
		scope = BASE;
		pasteText = '';
		pasted = { filled, skipped: out.skipped };
	}

	onDestroy(() => {
		if (waitTimer) clearTimeout(waitTimer);
	});

	function hint(key: ConfigKey): string {
		const parts = [];
		if (key.min !== null && key.min !== undefined) parts.push(`min ${key.min}`);
		if (key.max !== null && key.max !== undefined) parts.push(`max ${key.max}`);
		if (key.format) parts.push(`format ${key.format}`);
		if (key.off_allowed) parts.push('empty turns it off');
		return parts.join(', ');
	}

	const scopeLabel = (s: Scope) => (s === BASE ? 'Base' : `Lane ${s}`);
	const input =
		'min-h-11 w-full rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:opacity-60';
</script>

<svelte:head><title>Config · Casino dashboard</title></svelte:head>

<Guard perm="config.write" {service}>
	<div class="mx-auto flex max-w-4xl flex-col gap-4">
		<h1 class="text-xl font-semibold">Config</h1>

		{#if loadError}
			<Notice tone="error">{loadError}</Notice>
		{:else if !config}
			<p class="text-sm text-muted" role="status">Loading config...</p>
		{:else}
			{#if marks && applied.length}
				<Notice tone="success">
					{#each applied as entry (entry.lane)}
						<span class="block"
							>Applied on lane {entry.lane}: {entry.keys.join(', ') || 'no keys'}</span
						>
					{/each}
				</Notice>
			{:else if marks && waitedOut}
				<Notice tone="error">No reload seen after 10 s. Check the bot's logs.</Notice>
			{:else if restartOnly}
				<Notice>Saved. These changes apply after a restart.</Notice>
			{/if}

			<details class="rounded-lg border border-line bg-surface p-4">
				<summary class="min-h-11 cursor-pointer content-center text-sm font-medium"
					>Paste config</summary
				>
				<div class="mt-3 flex flex-col gap-3">
					<label for="paste-config" class="text-sm font-medium">Config to paste</label>
					<textarea
						id="paste-config"
						rows="8"
						spellcheck="false"
						autocomplete="off"
						class="{input} py-2 font-mono text-sm"
						placeholder="key=value lines, or the output of wolfctl config-show"
						bind:value={pasteText}></textarea>
					<p class="text-xs text-muted">
						Fills the Base tab. Nothing is saved until you Preview and Save. The API key is never
						taken from the paste.
					</p>
					<div>
						<Button disabled={!pasteText.trim()} onclick={fillFromPaste}>Fill form</Button>
					</div>
				</div>
			</details>
			{#if pasted}
				<Notice>
					<span class="block">Filled {pasted.filled} fields.</span>
					{#if pasted.skipped.length}
						<span class="block"
							>Not set: {pasted.skipped.map((s) => `${s.key} (${s.reason})`).join(', ')}</span
						>
					{/if}
				</Notice>
			{/if}

			<div role="tablist" aria-label="Config scope" class="flex flex-wrap gap-2">
				{#each scopes as s (s)}
					<button
						type="button"
						role="tab"
						aria-selected={scope === s}
						onclick={() => (scope = s)}
						class="min-h-11 rounded-md border border-line-strong px-4 text-sm aria-selected:bg-raised aria-selected:font-semibold"
						>{scopeLabel(s)}</button
					>
				{/each}
			</div>

			<div
				role="tabpanel"
				aria-label="{scopeLabel(scope)} settings"
				class="rounded-lg border border-line bg-surface p-4"
			>
				<ul class="flex flex-col gap-4">
					{#if scope === BASE && edits[BASE]?.[LANE_SPLIT]}
						<li class="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-start sm:gap-4">
							<label for="field-lane_split" class="pt-3 text-sm font-medium">lane_split</label>
							<div class="flex flex-col gap-1">
								<select
									id="field-lane_split"
									class={input}
									bind:value={edits[BASE][LANE_SPLIT].value}
								>
									{#each config.split_choices as choice (choice)}
										<option value={choice}>{choice}</option>
									{/each}
								</select>
								<p class="text-xs text-muted">How the budget or bet is shared between lanes.</p>
							</div>
						</li>
					{/if}
					{#each keys as key (key.key)}
						{@const edit = edits[scope]?.[key.key]}
						{#if edit}
							{@const id = `field-${scope}-${key.key}`}
							{@const locked = scope !== BASE && edit.useBase}
							<li class="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-start sm:gap-4">
								<div class="pt-3">
									<label for={id} class="text-sm font-medium">{key.key}</label>
									<span class="ml-2 {badgeClass(key.live_reload ? 'live' : 'restart')}"
										>{key.live_reload ? 'live' : 'restart required'}</span
									>
								</div>
								<div class="flex flex-col gap-1">
									{#if scope !== BASE}
										<label class="flex min-h-11 items-center gap-2 text-sm">
											<input
												type="checkbox"
												class="size-5"
												bind:checked={edit.useBase}
												onchange={() => {
													if (edit.useBase && config) edit.value = display(config.base[key.key]);
												}}
											/>
											Use base
										</label>
									{/if}
									{#if fieldKind(key) === 'select'}
										<select {id} class={input} disabled={locked} bind:value={edit.value}>
											{#if key.off_allowed}<option value="">off</option>{/if}
											{#each key.choices ?? [] as choice (choice)}
												<option value={choice}>{choice}</option>
											{/each}
										</select>
									{:else if fieldKind(key) === 'boolean'}
										<select {id} class={input} disabled={locked} bind:value={edit.value}>
											<option value="true">yes</option>
											<option value="false">no</option>
										</select>
									{:else if fieldKind(key) === 'long'}
										<textarea
											{id}
											rows="4"
											spellcheck="false"
											class="{input} font-mono"
											disabled={locked}
											bind:value={edit.value}></textarea>
									{:else}
										<input
											{id}
											type="text"
											inputmode={fieldKind(key) === 'number' ? 'decimal' : 'text'}
											autocomplete="off"
											class={input}
											disabled={locked}
											bind:value={edit.value}
										/>
									{/if}
									<p class="text-xs text-muted">
										{key.help ?? ''}{#if hint(key)}
											<span class="block">{hint(key)}</span>{/if}
									</p>
								</div>
							</li>
						{/if}
					{/each}
				</ul>
			</div>

			<div class="flex flex-wrap items-center gap-3">
				<Button
					variant="primary"
					disabled={changes.length === 0 || previewing}
					onclick={() => void runPreview()}
				>
					{previewing ? 'Checking...' : 'Preview'}
				</Button>
				<Button disabled={!previewFresh || saving} onclick={() => void save(false)}>Save</Button>
				{#if previewFresh && preview && needsRestart(preview)}
					<Button variant="danger" disabled={saving} onclick={() => void save(true)}>
						Save + restart
					</Button>
				{/if}
				<span class="text-sm text-muted" aria-live="polite">
					{changes.length === 0
						? 'No changes.'
						: previewFresh
							? `${changes.length} change(s) checked.`
							: 'Preview your changes before saving.'}
				</span>
			</div>

			{#if previewError}
				<Notice tone="error">{previewError}</Notice>
			{/if}

			{#if preview && previewFresh}
				<ConfigDiff {preview} />
			{/if}
		{/if}
	</div>
</Guard>
