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
		laneScopes,
		costLabel,
		needsRestart,
		restartPlan,
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
	import { laneLabel } from '#lib/lanes';
	import RulesBuilder from '#components/RulesBuilder';
	import { withVaultChoices, type VaultKey } from '#lib/keys';
	import { BAD_JSON, parseRules } from '#lib/rules';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import { can } from '#lib/permissions';
	import { services } from '#lib/services.svelte';
	import { api, session } from '#lib/session.svelte';

	const OWNER_ONLY = 'Only the owner can change this.';
	import Button from '#ui/Button';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';

	const service = $derived(page.params.service ?? '');
	const ctl = $derived(services.info(service)?.ctl ?? 'ctl');
	const isOwner = $derived(session.me?.roles.some((r) => r.role === 'owner') ?? false);
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
	let vault = $state<VaultKey[] | null>(null);

	const scopes = $derived(config ? [BASE, ...tabsOf(config)] : [BASE]);
	const keys = $derived.by(() => {
		if (!config) return [];
		const fields = config.keys.filter((k) => scope === BASE || k.lane_overridable);
		return vault ? withVaultChoices(fields, vault) : fields;
	});
	const changes: ConfigChange[] = $derived(config ? changesFrom(config, edits) : []);
	const changesKey = $derived(JSON.stringify(changes));
	const brokenRules = $derived(
		config
			? config.keys.some(
					(k) =>
						k.format === 'rules' &&
						Object.entries(edits).some(([name, scope]) => {
							const edit = scope[k.key];
							return (
								edit !== undefined &&
								(name === BASE || !edit.useBase) &&
								parseRules(edit.value).error === BAD_JSON
							);
						})
				)
			: false
	);
	const previewFresh = $derived(preview !== null && previewFor === changesKey);
	const applied = $derived(marks ? appliedSince(live.lanes, marks) : []);

	async function load() {
		try {
			const loaded = await unwrap(api.GET('/{service}/config', { params: { path: { service } } }));
			edits = Object.fromEntries(
				[BASE, ...tabsOf(loaded)].map((s) => [s, initialEdits(loaded, s)])
			);
			config = loaded;
			loadError = null;
			const tab = page.url.searchParams.get('tab');
			if (tab && [BASE, ...tabsOf(loaded)].includes(tab)) scope = tab;
		} catch (e) {
			loadError = e instanceof ApiError ? e.detail : String(e);
		}
	}

	function tabsOf(loaded: ConfigOut): Scope[] {
		return laneScopes(loaded.lane_ids, loaded.override_lanes);
	}

	$effect(() => {
		if (service && can(session.me, 'config.write', service)) void load();
	});

	async function loadVault() {
		try {
			const out = await unwrap(api.GET('/{service}/keys', { params: { path: { service } } }));
			vault = out.keys;
		} catch {
			vault = null;
		}
	}

	$effect(() => {
		if (service && can(session.me, 'keys.manage', service)) void loadVault();
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

	/** Save, then restart only the running lanes the change affects: at each lane's session end
	 * by default, or now when asked; a stopped lane picks the change up when it starts. */
	async function save(restart: 'none' | 'end' | 'now') {
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
		if (restart === 'none' || !config) return;
		const action = restart === 'end' ? 'lane_restart_end' : 'lane_restart';
		for (const lane of restartPlan(shown, config).filter((n) => !isIdle(n))) {
			const done = await control.ask(action, { lane });
			if (!done || done.status === 'failed') return;
		}
	}

	/** A lane known not to run picks the change up when it starts, so it needs no restart. */
	function isIdle(lane: number): boolean {
		const status = live.lanes[lane]?.status;
		return status === 'stopped' || status === 'down';
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

	const scopeLabel = (s: Scope) => (s === BASE ? 'Base' : laneLabel(Number(s), live.lanes));
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
						placeholder="key=value lines, or the output of {ctl} config-show"
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
							{@const ownerLocked = scope === BASE && key.owner_only && !isOwner}
							{@const locked = (scope !== BASE && edit.useBase) || ownerLocked}
							<li class="grid gap-1 sm:grid-cols-[14rem_1fr] sm:items-start sm:gap-4">
								<div class="pt-3">
									<label for={id} class="text-sm font-medium">{key.key}</label>
									<span class="ml-2 {badgeClass(key.live_reload ? 'live' : 'restart')}"
										>{costLabel(key.live_reload ? 'live' : 'restart', scope)}</span
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
									{:else if fieldKind(key) === 'long' && key.rule_options}
										<RulesBuilder
											{id}
											{input}
											options={key.rule_options}
											disabled={locked}
											bind:value={edit.value}
										/>
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
											<span class="block">{hint(key)}</span>{/if}{#if ownerLocked}
											<span class="block">{OWNER_ONLY}</span>{/if}
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
					disabled={changes.length === 0 || previewing || brokenRules}
					onclick={() => void runPreview()}
				>
					{previewing ? 'Checking...' : 'Preview'}
				</Button>
				<Button disabled={!previewFresh || saving} onclick={() => void save('none')}>Save</Button>
				{#if previewFresh && preview && needsRestart(preview)}
					<Button disabled={saving} onclick={() => void save('end')}>
						Save + restart at session end
					</Button>
					<Button variant="danger" disabled={saving} onclick={() => void save('now')}>
						Save + restart now
					</Button>
				{/if}
				<span class="text-sm text-muted" aria-live="polite">
					{brokenRules
						? 'Fix the rules JSON before Preview.'
						: changes.length === 0
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
				<ConfigDiff {preview} costs lanes={live.lanes} />
			{/if}
		{/if}
	</div>
</Guard>
