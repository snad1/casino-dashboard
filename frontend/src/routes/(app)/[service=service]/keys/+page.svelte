<script lang="ts">
	import { page } from '$app/state';
	import { ApiError, unwrap } from '#lib/api/client';
	import {
		keyIdFor,
		labelError,
		secretError,
		usedBy,
		type KeyKind,
		type VaultKey
	} from '#lib/keys';
	import { api } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import ConfirmDialog from '#ui/ConfirmDialog';
	import Field from '#ui/Field';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';
	import StepUpPrompt from '#ui/StepUpPrompt';

	type Write = () => Promise<string>;

	const service = $derived(page.params.service ?? '');
	const KINDS: { value: KeyKind; label: string }[] = [
		{ value: 'key', label: 'Casino key' },
		{ value: 'proxy', label: 'Proxy with a password' }
	];

	let keys = $state<VaultKey[] | null>(null);
	let loadError = $state<string | null>(null);
	let mine = $state(false);
	let label = $state('');
	let kind = $state<KeyKind>('key');
	let secret = $state('');
	let rotating = $state<string | null>(null);
	let newSecret = $state('');
	let removing = $state<VaultKey | null>(null);
	let confirmOpen = $state(false);
	let stepUpOpen = $state(false);
	let busy = $state(false);
	let notice = $state<{ tone: 'success' | 'error'; text: string } | null>(null);
	let retry: Write | null = null;

	const nameError = $derived(label === '' ? null : labelError(label));
	const keyError = $derived(secret === '' ? null : secretError(kind, secret));
	const canAdd = $derived(
		label !== '' && secret !== '' && nameError === null && keyError === null && !busy
	);
	const rotateKind = $derived(keys?.find((k) => k.id === rotating)?.kind ?? 'key');
	const rotateError = $derived(newSecret === '' ? null : secretError(rotateKind, newSecret));
	const date = (iso: string | null) => (iso ? iso.slice(0, 10) : '-');

	function message(e: unknown): string {
		return e instanceof ApiError ? e.detail : 'Something went wrong. Please try again.';
	}

	async function load() {
		try {
			const out = await unwrap(
				api.GET('/{service}/keys', { params: { path: { service }, query: { mine } } })
			);
			keys = out.keys;
			loadError = null;
		} catch (e) {
			loadError = message(e);
		}
	}

	$effect(() => {
		if (service) void load();
	});

	async function perform(write: Write) {
		busy = true;
		notice = null;
		try {
			const text = await write();
			retry = null;
			notice = { tone: 'success', text };
			await load();
		} catch (e) {
			if (e instanceof ApiError && e.flags.has('step_up_required')) {
				retry = write;
				stepUpOpen = true;
				return;
			}
			retry = null;
			notice = { tone: 'error', text: message(e) };
		} finally {
			busy = false;
		}
	}

	function add(event: SubmitEvent) {
		event.preventDefault();
		if (!canAdd) return;
		const body = { label, kind, secret };
		void perform(async () => {
			const out = await unwrap(
				api.POST('/{service}/keys', { params: { path: { service } }, body })
			);
			label = '';
			secret = '';
			return `${out.key.label} saved as ${out.key.id}.`;
		});
	}

	function rotate(event: SubmitEvent, key: VaultKey) {
		event.preventDefault();
		if (newSecret === '' || rotateError) return;
		const body = { secret: newSecret };
		void perform(async () => {
			const out = await unwrap(
				api.POST('/{service}/keys/{key_id}/rotate', {
					params: { path: { service, key_id: key.id } },
					body
				})
			);
			newSecret = '';
			rotating = null;
			return out.note ?? `${out.key.label} replaced.`;
		});
	}

	function remove() {
		const key = removing;
		confirmOpen = false;
		if (!key) return;
		void perform(async () => {
			await unwrap(
				api.DELETE('/{service}/keys/{key_id}', { params: { path: { service, key_id: key.id } } })
			);
			removing = null;
			return `${key.label} deleted.`;
		});
	}

	const section = 'rounded-lg border border-line bg-surface p-4';
	const input =
		'min-h-11 rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';
</script>

<svelte:head><title>Keys · Casino dashboard</title></svelte:head>

<Guard perm="keys.manage" {service}>
	<div class="mx-auto flex max-w-4xl flex-col gap-4">
		<h1 class="text-xl font-semibold">Keys</h1>
		<p class="text-sm text-muted">
			Casino keys and proxies live encrypted on the bot host. They are never shown again after you
			save them; a lane picks one by name on the Config page.
		</p>

		{#if notice}
			<Notice tone={notice.tone}>{notice.text}</Notice>
		{/if}

		<section class={section} aria-labelledby="add-title">
			<h2 id="add-title" class="mb-3 font-semibold">Add a key</h2>
			<form class="grid gap-3 sm:grid-cols-2" onsubmit={add}>
				<Field
					label="Name"
					bind:value={label}
					maxlength={64}
					autocomplete="off"
					error={nameError}
					hint={keyIdFor(label) ? `Saved as ${keyIdFor(label)}` : undefined}
				/>
				<label class="flex flex-col gap-1 text-sm font-medium">
					Kind
					<select class={input} bind:value={kind}>
						{#each KINDS as option (option.value)}
							<option value={option.value}>{option.label}</option>
						{/each}
					</select>
				</label>
				<div class="sm:col-span-2">
					<Field
						label={kind === 'proxy' ? 'Proxy URL' : 'Key'}
						type="password"
						bind:value={secret}
						autocomplete="off"
						spellcheck="false"
						error={keyError}
						hint={kind === 'proxy' ? 'socks5://user:password@host:port' : undefined}
					/>
				</div>
				<div>
					<Button type="submit" variant="primary" disabled={!canAdd}>Add key</Button>
				</div>
			</form>
		</section>

		<section class={section} aria-labelledby="list-title">
			<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
				<h2 id="list-title" class="font-semibold">Saved keys</h2>
				<label class="flex items-center gap-2 text-sm">
					<input type="checkbox" class="size-5" bind:checked={mine} onchange={() => void load()} />
					Only mine
				</label>
			</div>
			{#if loadError}
				<Notice tone="error">{loadError}</Notice>
			{:else if keys === null}
				<p class="text-sm text-muted" role="status">Loading keys...</p>
			{:else if keys.length === 0}
				<p class="text-sm text-muted">No keys yet.</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full text-left text-sm" aria-label="Saved keys">
						<thead class="text-muted">
							<tr>
								<th class="py-2">Name</th>
								<th>Kind</th>
								<th>Last 4</th>
								<th>Used by</th>
								<th>Added</th>
								<th>Rotated</th>
								<th><span class="sr-only">Actions</span></th>
							</tr>
						</thead>
						<tbody>
							{#each keys as key (key.id)}
								<tr class="border-t border-line align-top">
									<td class="py-2">
										<span class="font-medium break-all">{key.label}</span>
										<span class="block font-mono text-xs text-muted">{key.id}</span>
									</td>
									<td>{key.kind}</td>
									<td class="font-mono">••••{key.last4}</td>
									<td>{usedBy(key.used_by_lanes)}</td>
									<td>{date(key.created_at)}</td>
									<td>{date(key.rotated_at)}</td>
									<td class="py-2">
										<div class="flex flex-wrap justify-end gap-2">
											<Button
												onclick={() => {
													rotating = rotating === key.id ? null : key.id;
													newSecret = '';
												}}>Rotate</Button
											>
											<Button
												variant="danger"
												onclick={() => {
													removing = key;
													confirmOpen = true;
												}}>Delete</Button
											>
										</div>
									</td>
								</tr>
								{#if rotating === key.id}
									<tr>
										<td colspan="7" class="pb-3">
											<form
												class="flex flex-wrap items-end gap-3"
												aria-label="Rotate {key.label}"
												onsubmit={(e) => rotate(e, key)}
											>
												<div class="min-w-64 flex-1">
													<Field
														label={key.kind === 'proxy' ? 'New proxy URL' : 'New key'}
														type="password"
														bind:value={newSecret}
														autocomplete="off"
														spellcheck="false"
														error={rotateError}
													/>
												</div>
												<Button
													type="submit"
													variant="primary"
													disabled={newSecret === '' || rotateError !== null || busy}
													>Save new key</Button
												>
											</form>
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	</div>

	<ConfirmDialog
		bind:open={confirmOpen}
		title="Delete {removing?.label ?? 'this key'}?"
		body="The key is removed from the bot host for good. A key a lane uses can't be deleted."
		confirmLabel="Delete key"
		danger
		{busy}
		onconfirm={remove}
		oncancel={() => (removing = null)}
	/>
	<StepUpPrompt bind:open={stepUpOpen} onverified={() => retry && perform(retry)} />
</Guard>
