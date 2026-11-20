<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		COOLDOWNS,
		CUSTOM,
		KINDS,
		bodyFor,
		changes,
		cooldownLabel,
		describe,
		emptyForm,
		formFor,
		sentTo,
		type AlertEvent,
		type Kind,
		type Rule,
		type RuleBody,
		type RuleForm
	} from '#lib/alerts';
	import { LANES, formatTime } from '#lib/analytics';
	import { ApiError, unwrap } from '#lib/api/client';
	import { can } from '#lib/permissions';
	import { DEFAULT_SERVICE, api, session } from '#lib/session.svelte';
	import { toast } from '#lib/toast.svelte';
	import Button from '#ui/Button';
	import ConfirmDialog from '#ui/ConfirmDialog';
	import Field from '#ui/Field';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';

	const service = DEFAULT_SERVICE;
	const path = { service };
	const manage = $derived(can(session.me, 'alerts.manage', service));

	let rules = $state<Rule[] | null>(null);
	let events = $state<AlertEvent[]>([]);
	let nextBefore = $state<number | null>(null);
	let loadError = $state<string | null>(null);
	let editing = $state<Rule | null>(null);
	let form = $state<RuleForm | null>(null);
	let formError = $state<string | null>(null);
	let pending = $state<RuleBody | null>(null);
	let confirmSave = $state(false);
	let removing = $state<Rule | null>(null);
	let confirmDelete = $state(false);
	let busy = $state(false);

	const kinds = Object.entries(KINDS) as [Kind, (typeof KINDS)[Kind]][];
	const fields = $derived(form ? KINDS[form.kind].fields : []);
	const cooldowns = $derived(
		form && !COOLDOWNS.some((o) => o.value === form?.cooldown)
			? [...COOLDOWNS, { value: form.cooldown, label: cooldownLabel(Number(form.cooldown)) }]
			: COOLDOWNS
	);

	function message(e: unknown): string {
		return e instanceof ApiError ? e.detail : String(e);
	}

	async function load() {
		try {
			const [ruleList, page] = await Promise.all([
				unwrap(api.GET('/{service}/alerts/rules', { params: { path } })),
				unwrap(api.GET('/{service}/alerts/events', { params: { path, query: { limit: 50 } } }))
			]);
			rules = ruleList;
			events = page.items;
			nextBefore = page.next_before ?? null;
			loadError = null;
		} catch (e) {
			loadError = message(e);
		}
	}

	async function older() {
		if (nextBefore === null) return;
		try {
			const page = await unwrap(
				api.GET('/{service}/alerts/events', {
					params: { path, query: { limit: 50, before: nextBefore } }
				})
			);
			events = [...events, ...page.items];
			nextBefore = page.next_before ?? null;
		} catch (e) {
			toast('error', message(e));
		}
	}

	$effect(() => {
		if (session.me) void load();
	});

	function startNew() {
		editing = null;
		form = emptyForm();
		formError = null;
	}

	function startEdit(rule: Rule) {
		editing = rule;
		form = formFor(rule);
		formError = null;
	}

	function pickKind(kind: Kind) {
		if (!form) return;
		const fresh = emptyForm(kind);
		const keepName = editing !== null || form.name !== KINDS[form.kind].label;
		form = {
			...fresh,
			name: keepName ? form.name : fresh.name,
			lane: form.lane,
			cooldown: form.cooldown,
			enabled: form.enabled
		};
	}

	function review(event: SubmitEvent) {
		event.preventDefault();
		if (!form) return;
		const result = bodyFor(form);
		if ('error' in result) {
			formError = result.error;
			return;
		}
		formError = null;
		pending = result.body;
		confirmSave = true;
	}

	async function save() {
		if (!pending) return;
		busy = true;
		try {
			if (editing) {
				await unwrap(
					api.PUT('/{service}/alerts/rules/{rule_id}', {
						params: { path: { service, rule_id: editing.id } },
						body: pending
					})
				);
			} else {
				await unwrap(api.POST('/{service}/alerts/rules', { params: { path }, body: pending }));
			}
			toast('success', 'Rule saved.');
			form = null;
			editing = null;
			await load();
		} catch (e) {
			formError = message(e);
		} finally {
			busy = false;
			confirmSave = false;
		}
	}

	async function remove() {
		if (!removing) return;
		busy = true;
		try {
			await unwrap(
				api.DELETE('/{service}/alerts/rules/{rule_id}', {
					params: { path: { service, rule_id: removing.id } }
				})
			);
			toast('success', 'Rule deleted.');
			await load();
		} catch (e) {
			toast('error', message(e));
		} finally {
			busy = false;
			confirmDelete = false;
			removing = null;
		}
	}

	async function test(rule: Rule) {
		try {
			const out = await unwrap(
				api.POST('/{service}/alerts/rules/{rule_id}/test', {
					params: { path: { service, rule_id: rule.id } }
				})
			);
			toast('success', sentTo(out.channels));
		} catch (e) {
			toast('error', message(e));
		}
	}

	const input = 'min-h-11 rounded-md border border-line-strong bg-surface px-3';
	const cell = 'p-2 align-top';
</script>

<svelte:head><title>Alerts · Casino dashboard</title></svelte:head>

<Guard perm="view">
	<div class="mx-auto flex max-w-5xl flex-col gap-4">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h1 class="text-xl font-semibold">Alerts</h1>
			<div class="flex flex-wrap gap-3">
				<a
					class="inline-flex min-h-11 items-center rounded-md border border-line-strong px-4 text-sm font-medium"
					href={resolve('/(app)/alerts/settings')}>My alert settings</a
				>
				{#if manage}
					<Button variant="primary" onclick={startNew}>New rule</Button>
				{/if}
			</div>
		</div>

		{#if loadError}
			<Notice tone="error">{loadError}</Notice>
		{:else if rules === null}
			<p class="text-sm text-muted" aria-live="polite">Loading alerts...</p>
		{/if}

		{#if form}
			<form
				onsubmit={review}
				class="flex flex-col gap-4 rounded-lg border border-line bg-surface p-4"
				aria-labelledby="editor-title"
			>
				<h2 id="editor-title" class="font-semibold">{editing ? 'Edit rule' : 'New rule'}</h2>
				<div class="grid gap-4 sm:grid-cols-2">
					<label class="flex flex-col gap-1 text-sm font-medium">
						Alert when
						<select
							class={input}
							value={form.kind}
							onchange={(e) => pickKind(e.currentTarget.value as Kind)}
						>
							{#each kinds as [kind, spec] (kind)}
								<option value={kind}>{spec.label}</option>
							{/each}
						</select>
					</label>
					<Field label="Name" bind:value={form.name} maxlength={80} required />
					<label class="flex flex-col gap-1 text-sm font-medium">
						Lane
						<select class={input} bind:value={form.lane}>
							<option value="all">All lanes</option>
							{#each LANES as n (n)}
								<option value={String(n)}>Lane {n}</option>
							{/each}
						</select>
					</label>
					<label class="flex flex-col gap-1 text-sm font-medium">
						Cooldown
						<select class={input} bind:value={form.cooldown}>
							{#each cooldowns as option (option.value)}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
					</label>
					{#each fields as field (field.key)}
						{#if field.type === 'choice'}
							<div class="flex flex-col gap-2">
								<label class="flex flex-col gap-1 text-sm font-medium">
									{field.label}
									<select class={input} bind:value={form.values[field.key]}>
										{#if field.optional}
											<option value="">{field.optional}</option>
										{/if}
										{#each field.options as option (option.value)}
											<option value={option.value}>{option.label}</option>
										{/each}
										{#if field.custom}
											<option value={CUSTOM}>Custom...</option>
										{/if}
									</select>
								</label>
								{#if form.values[field.key] === CUSTOM}
									<Field
										label="Custom {field.label.toLowerCase()}"
										inputmode="decimal"
										bind:value={form.custom[field.key]}
									/>
								{/if}
							</div>
						{:else if field.type === 'many'}
							<fieldset class="flex flex-col gap-2 sm:col-span-2">
								<legend class="text-sm font-medium">{field.label}</legend>
								<div class="flex flex-wrap gap-x-4 gap-y-2">
									{#each field.options as option (option.value)}
										<label class="flex min-h-11 items-center gap-2 text-sm">
											<input
												type="checkbox"
												class="size-5"
												value={option.value}
												bind:group={form.many[field.key]}
											/>
											{option.label}
										</label>
									{/each}
								</div>
							</fieldset>
						{:else}
							<Field
								label={field.label}
								hint={field.hint}
								maxlength={200}
								bind:value={form.values[field.key]}
							/>
						{/if}
					{/each}
					<label class="flex min-h-11 items-center gap-2 text-sm font-medium">
						<input type="checkbox" class="size-5" bind:checked={form.enabled} />
						Rule is on
					</label>
				</div>
				{#if formError}
					<Notice tone="error">{formError}</Notice>
				{/if}
				<div class="flex gap-3">
					<Button variant="primary" type="submit">Save</Button>
					<button
						type="button"
						class="min-h-11 rounded-md border border-line-strong px-4 text-sm"
						onclick={() => (form = null)}>Cancel</button
					>
				</div>
			</form>
		{/if}

		{#if rules}
			<!-- A table that scrolls sideways must take focus so a keyboard can scroll it. -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<section
				class="overflow-x-auto rounded-lg border border-line bg-surface p-4"
				aria-label="Rules"
				tabindex="0"
			>
				<table class="w-full text-left text-sm">
					<caption class="mb-2 text-left font-semibold">Rules</caption>
					<thead class="text-muted">
						<tr>
							<th scope="col" class="p-2">Name</th>
							<th scope="col" class="p-2">Watches</th>
							<th scope="col" class="p-2">Lane</th>
							<th scope="col" class="p-2">Cooldown</th>
							<th scope="col" class="p-2">State</th>
							{#if manage}<th scope="col" class="p-2"><span class="sr-only">Actions</span></th>{/if}
						</tr>
					</thead>
					<tbody>
						{#each rules as rule (rule.id)}
							<tr class="border-t border-line">
								<th scope="row" class="{cell} font-medium">{rule.name}</th>
								<td class={cell}>{describe(rule)}</td>
								<td class={cell}>{rule.lane === null ? 'All' : `Lane ${rule.lane}`}</td>
								<td class={cell}>{cooldownLabel(rule.cooldown_s)}</td>
								<td class={cell}>{rule.enabled ? 'On' : 'Off'}</td>
								{#if manage}
									<td class="{cell} whitespace-nowrap">
										<button
											type="button"
											class="min-h-11 px-2 text-accent underline"
											aria-label="Edit {rule.name}"
											onclick={() => startEdit(rule)}>Edit</button
										>
										<button
											type="button"
											class="min-h-11 px-2 text-accent underline"
											aria-label="Test {rule.name}"
											onclick={() => test(rule)}>Test</button
										>
										<button
											type="button"
											class="min-h-11 px-2 text-loss underline"
											aria-label="Delete {rule.name}"
											onclick={() => {
												removing = rule;
												confirmDelete = true;
											}}>Delete</button
										>
									</td>
								{/if}
							</tr>
						{:else}
							<tr><td class={cell} colspan="6">No rules yet.</td></tr>
						{/each}
					</tbody>
				</table>
			</section>

			<!-- A table that scrolls sideways must take focus so a keyboard can scroll it. -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<section
				class="overflow-x-auto rounded-lg border border-line bg-surface p-4"
				aria-label="Alert history"
				tabindex="0"
			>
				<table class="w-full text-left text-sm">
					<caption class="mb-2 text-left font-semibold">Alert history</caption>
					<thead class="text-muted">
						<tr>
							<th scope="col" class="p-2">When</th>
							<th scope="col" class="p-2">Alert</th>
							<th scope="col" class="p-2">Lane</th>
						</tr>
					</thead>
					<tbody>
						{#each events as event (event.id)}
							<tr class="border-t border-line">
								<td class="{cell} whitespace-nowrap font-mono text-muted"
									>{formatTime(event.fired_at)}</td
								>
								<td class={cell}>{event.message}</td>
								<td class={cell}>{event.lane ?? 'All'}</td>
							</tr>
						{:else}
							<tr><td class={cell} colspan="3">Nothing has fired yet.</td></tr>
						{/each}
					</tbody>
				</table>
				{#if nextBefore !== null}
					<button
						type="button"
						class="mt-3 min-h-11 rounded-md border border-line-strong px-4 text-sm"
						onclick={older}>Older</button
					>
				{/if}
			</section>
		{/if}
	</div>

	<ConfirmDialog
		bind:open={confirmSave}
		title="Save rule?"
		body={pending ? changes(editing, pending).join('; ') : ''}
		confirmLabel="Save rule"
		{busy}
		onconfirm={save}
	/>
	<ConfirmDialog
		bind:open={confirmDelete}
		title="Delete {removing?.name ?? 'rule'}?"
		body="The rule stops firing. Alerts it already sent stay in the history."
		confirmLabel="Delete rule"
		danger
		{busy}
		onconfirm={remove}
		oncancel={() => (removing = null)}
	/>
</Guard>
