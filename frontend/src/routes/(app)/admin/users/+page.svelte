<script lang="ts">
	import { onMount } from 'svelte';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import Checklist from '#components/Checklist';
	import { keysText, laneLabel, lanesText } from '#lib/lanes';
	import { liveFor } from '#lib/live/index';
	import { ON_SOME_KEYS, ON_SOME_LANES, ROLE_INFO } from '#lib/permissions';
	import { services } from '#lib/services.svelte';
	import { api, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import ConfirmDialog from '#ui/ConfirmDialog';
	import Field from '#ui/Field';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';
	import StepUpPrompt from '#ui/StepUpPrompt';

	type User = components['schemas']['UserOut'];
	type Role = 'admin' | 'operator' | 'viewer' | 'lane_manager';
	type Pending = {
		title: string;
		body: string;
		label: string;
		danger: boolean;
		run: () => Promise<void>;
	};
	/** What a viewer or operator role is narrowed to: some lanes, or some keys, never both. */
	type Limit = { lanes: number[]; keys: string[] };
	type VaultKey = { id: string; label: string; last4: string };

	const ROLES: Role[] = ['viewer', 'operator', 'admin', 'lane_manager'];
	const LANE_ROLES = new Set<Role>(['viewer', 'operator']);
	/** The picked service: where lanes, keys and quotas apply. A user manager always has one. */
	const here = $derived(services.picked() ?? '');
	const live = $derived(liveFor(here));
	let users: User[] = $state([]);
	let inviteEmail = $state('');
	let inviteRole: Role = $state('viewer');
	let inviteScope = $state('all');
	let inviteLimit: Limit = $state({ lanes: [], keys: [] });
	let registry: number[] = $state([]);
	let vault: VaultKey[] = $state([]);
	let drafts: Record<string, Limit | undefined> = $state({});
	let notice: { tone: 'success' | 'error'; text: string } | null = $state(null);
	let pending: Pending | null = $state(null);
	let confirmOpen = $state(false);
	let stepUpOpen = $state(false);
	let busy = $state(false);

	const isOwner = $derived(session.me?.roles.some((r) => r.role === 'owner') ?? false);
	const lanes = (n: number) => `${n} lane${n === 1 ? '' : 's'}`;

	const NONE: Limit = { lanes: [], keys: [] };
	const LIMIT_HINT = 'A role is limited by lanes or by keys, not both.';
	const userLimit = (user: User): Limit => ({
		lanes: user.roles.map((r) => r.lane).filter((n): n is number => typeof n === 'number'),
		keys: user.roles.map((r) => r.key_ref).filter((k): k is string => typeof k === 'string')
	});
	const laneChoices = $derived(
		[
			...new Set([
				...registry,
				...Object.keys(live.lanes).map(Number),
				...users.flatMap((u) => userLimit(u).lanes)
			])
		]
			.filter((n) => Number.isInteger(n) && n >= 1)
			.sort((a, b) => a - b)
	);
	const keyChoices = $derived(
		[...new Set([...vault.map((k) => k.id), ...users.flatMap((u) => userLimit(u).keys)])].sort()
	);
	const label = (lane: number) => laneLabel(lane, live.lanes);
	const keyLabel = (id: string) => {
		const key = vault.find((k) => k.id === id);
		return key ? `${key.label} · ...${key.last4}` : id;
	};
	const same = <T,>(a: T[], b: T[]) => a.length === b.length && a.every((n) => b.includes(n));
	const sameLimit = (a: Limit, b: Limit) => same(a.lanes, b.lanes) && same(a.keys, b.keys);
	const limitText = (limit: Limit) =>
		limit.keys.length ? keysText(limit.keys) : lanesText(limit.lanes);
	const scopeText = (role: string, service: string | null | undefined, limit: Limit) => {
		const narrowed = limitText(limit);
		return `${role}${service ? ` on ${service}` : ''}${narrowed ? ` ${narrowed}` : ''}`;
	};
	/** Picking lanes clears the keys and the other way round: the api takes one or the other. */
	const withLanes = (limit: Limit, lanes: number[]): Limit => ({
		lanes,
		keys: lanes.length ? [] : limit.keys
	});
	const withKeys = (limit: Limit, keys: string[]): Limit => ({
		keys,
		lanes: keys.length ? [] : limit.lanes
	});

	/** One phrase per role and service, its lanes or keys gathered: `viewer on wolfbet lanes 9, 10`. */
	function describe(user: User): string {
		const groups: Record<string, { role: string; service?: string | null; limit: Limit }> = {};
		for (const r of user.roles) {
			const group = (groups[`${r.role}|${r.service ?? ''}`] ??= {
				role: r.role,
				service: r.service,
				limit: { lanes: [], keys: [] }
			});
			if (typeof r.lane === 'number') group.limit.lanes.push(r.lane);
			if (typeof r.key_ref === 'string') group.limit.keys.push(r.key_ref);
		}
		return (
			Object.values(groups)
				.map((g) => scopeText(g.role, g.service, g.limit))
				.join(', ') || 'no role'
		);
	}

	async function refresh() {
		users = (await unwrap(api.GET('/admin/users'))).items;
	}

	/** The bot's lanes, so a friend can be given a lane that is not betting right now. */
	async function loadLanes(service: string) {
		registry = [];
		if (!service) return;
		const { data } = await api.GET('/{service}/config', { params: { path: { service } } });
		if (data) registry = data.lane_ids;
	}

	/** The bot's casino keys, so a friend can be given everything that bets with one. */
	async function loadKeys(service: string) {
		vault = [];
		if (!service) return;
		const { data } = await api.GET('/{service}/keys', { params: { path: { service } } });
		if (data) vault = data.keys.filter((k) => k.kind === 'key');
	}

	onMount(() => {
		refresh().catch((e) => (notice = { tone: 'error', text: message(e) }));
	});

	$effect(() => {
		void loadLanes(here);
		void loadKeys(here);
	});

	function message(e: unknown): string {
		return e instanceof ApiError ? e.detail : String(e);
	}

	async function perform(action: Pending) {
		busy = true;
		try {
			await action.run();
			confirmOpen = false;
			pending = null;
			await refresh();
		} catch (e) {
			if (e instanceof ApiError && e.flags.has('step_up_required')) {
				confirmOpen = false;
				stepUpOpen = true;
				return;
			}
			confirmOpen = false;
			notice = { tone: 'error', text: message(e) };
		} finally {
			busy = false;
		}
	}

	function ask(action: Pending) {
		notice = null;
		pending = action;
		confirmOpen = true;
	}

	/** A lane manager or a lane is always on the picked service, as on the role picker. */
	function invite(event: SubmitEvent) {
		event.preventDefault();
		const email = inviteEmail.trim();
		const role = inviteRole;
		const limit = LANE_ROLES.has(role) ? inviteLimit : NONE;
		const { lanes, keys } = limit;
		const service =
			role === 'lane_manager' || lanes.length || keys.length
				? here
				: inviteScope === 'all'
					? null
					: inviteScope;
		const scope = scopeText(role, service, limit);
		ask({
			title: `Invite ${email}?`,
			body: `They get an email with a link to set a password and join as ${scope}.`,
			label: 'Send invite',
			danger: false,
			run: async () => {
				await unwrap(api.POST('/auth/invites', { body: { email, role, service, lanes, keys } }));
				notice = { tone: 'success', text: `Invite sent to ${email}.` };
				inviteEmail = '';
				inviteLimit = { lanes: [], keys: [] };
			}
		});
	}

	function setQuota(user: User, quota: number | null) {
		if (quota === null) return;
		ask({
			title: `Let ${user.email} run ${lanes(quota)}?`,
			body: `They may create up to ${lanes(quota)} on ${here}, each with their own keys.`,
			label: 'Set quota',
			danger: false,
			run: async () => {
				await unwrap(
					api.PUT('/admin/users/{user_id}/lane-quota', {
						params: { path: { user_id: user.id } },
						body: { lane_quota: quota }
					})
				);
				notice = { tone: 'success', text: `${user.email} may now run ${lanes(quota)}.` };
			}
		});
	}

	/** A role on every service, or (with lanes or keys) on those of the picked service. */
	function changeRole(user: User, role: Role, chosen: Limit = userLimit(user)) {
		const limit = LANE_ROLES.has(role) ? chosen : NONE;
		const { lanes, keys } = limit;
		const narrowed = limitText(limit);
		const onService = role === 'lane_manager' || narrowed !== '';
		const where = narrowed ? `${here} ${narrowed}` : onService ? here : 'every service';
		ask({
			title: `Make ${user.email} ${role}${narrowed ? ` on ${narrowed}` : ''}?`,
			body: `Their current roles (${describe(user)}) are replaced by ${role} on ${where}.`,
			label: `Make ${role}`,
			danger: false,
			run: async () => {
				await unwrap(
					api.PUT('/admin/users/{user_id}/role', {
						params: { path: { user_id: user.id } },
						body: { role, service: onService ? here : null, lanes, keys }
					})
				);
				drafts[user.id] = undefined;
				notice = { tone: 'success', text: `${user.email} is now ${role} on ${where}.` };
			}
		});
	}

	function laneFrom(text: string): number | null {
		const lane = Number.parseInt(text, 10);
		return Number.isInteger(lane) && lane >= 1 && lane <= 99 ? lane : null;
	}

	function toggle(user: User) {
		const disable = !user.disabled;
		ask({
			title: `${disable ? 'Disable' : 'Enable'} ${user.email}?`,
			body: disable
				? 'They are signed out everywhere and cannot sign in until enabled again.'
				: 'They can sign in again with their password.',
			label: disable ? 'Disable account' : 'Enable account',
			danger: disable,
			run: async () => {
				const path = disable ? '/admin/users/{user_id}/disable' : '/admin/users/{user_id}/enable';
				await unwrap(api.POST(path, { params: { path: { user_id: user.id } } }));
				notice = { tone: 'success', text: `${user.email} is ${disable ? 'disabled' : 'enabled'}.` };
			}
		});
	}
</script>

<svelte:head><title>Users · Casino dashboard</title></svelte:head>

<Guard perm="users.manage">
	<div class="mx-auto flex max-w-4xl flex-col gap-6">
		<h1 class="text-xl font-semibold">Users</h1>
		{#if notice}<Notice tone={notice.tone}>{notice.text}</Notice>{/if}

		<form
			onsubmit={invite}
			class="grid gap-4 rounded-lg border border-line bg-surface p-4 sm:grid-cols-[1fr_auto_auto_auto] sm:items-end"
		>
			<Field label="Email to invite" type="email" bind:value={inviteEmail} required />
			<label class="flex flex-col gap-1 text-sm font-medium">
				Role
				<select
					bind:value={inviteRole}
					aria-describedby="invite-role-help"
					class="min-h-11 rounded-md border border-line-strong bg-surface px-3 text-ink"
				>
					{#each ROLES as role (role)}<option value={role}>{ROLE_INFO[role].name}</option>{/each}
				</select>
			</label>
			<label class="flex flex-col gap-1 text-sm font-medium">
				Service
				<select
					bind:value={inviteScope}
					class="min-h-11 rounded-md border border-line-strong bg-surface px-3 text-ink"
				>
					<option value="all">All services</option>
					{#each services.list as option (option.name)}
						<option value={option.name}>{option.label}</option>
					{/each}
				</select>
			</label>
			<Button type="submit" variant="primary">Invite</Button>
			{#if LANE_ROLES.has(inviteRole)}
				<div class="grid gap-4 sm:col-span-full sm:grid-cols-2">
					<Checklist
						legend="Lanes on {here}"
						choices={laneChoices}
						value={inviteLimit.lanes}
						{label}
						empty="No lanes reported by the bot yet."
						onchange={(next) => (inviteLimit = withLanes(inviteLimit, next))}
					/>
					<Checklist
						legend="Keys on {here}"
						choices={keyChoices}
						value={inviteLimit.keys}
						label={keyLabel}
						hint={LIMIT_HINT}
						empty="No casino keys in the vault."
						onchange={(next) => (inviteLimit = withKeys(inviteLimit, next))}
					/>
				</div>
			{/if}
		</form>
		<p id="invite-role-help" class="text-sm text-muted" aria-live="polite">
			{ROLE_INFO[inviteRole].name}: {ROLE_INFO[inviteRole].help}
		</p>
		<details class="rounded-lg border border-line bg-surface p-4" open>
			<summary class="cursor-pointer font-semibold">What each role can do</summary>
			<dl class="mt-3 grid gap-2 text-sm sm:grid-cols-[10rem_1fr]">
				{#each Object.values(ROLE_INFO) as info (info.name)}
					<dt class="font-medium">{info.name}</dt>
					<dd class="text-muted">{info.help}</dd>
				{/each}
				<dt class="font-medium">On some lanes</dt>
				<dd class="text-muted">{ON_SOME_LANES}</dd>
				<dt class="font-medium">On some keys</dt>
				<dd class="text-muted">{ON_SOME_KEYS}</dd>
			</dl>
		</details>

		<div class="overflow-x-auto">
			<table class="w-full text-left text-sm">
				<thead class="text-muted">
					<tr
						><th class="p-2">Email</th><th class="p-2">Role</th><th class="p-2">2FA</th><th
							class="p-2">Status</th
						><th class="p-2"><span class="sr-only">Actions</span></th></tr
					>
				</thead>
				<tbody>
					{#each users as user (user.id)}
						<tr class="border-t border-line">
							<td class="p-2">{user.email}</td>
							<td class="p-2">
								{#if user.roles.some((r) => r.role === 'owner')}
									owner
								{:else}
									<div class="flex flex-wrap items-center gap-2">
										<select
											aria-label="Role for {user.email}"
											value={user.roles[0]?.role ?? 'viewer'}
											onchange={(e) => changeRole(user, e.currentTarget.value as Role)}
											class="min-h-11 rounded-md border border-line-strong bg-surface px-2 text-ink"
										>
											{#each ROLES as role (role)}<option value={role}
													>{ROLE_INFO[role].name}</option
												>{/each}
										</select>
									</div>
									{#if LANE_ROLES.has((user.roles[0]?.role ?? 'viewer') as Role)}
										{@const saved = userLimit(user)}
										{@const draft = drafts[user.id] ?? saved}
										<details class="mt-2">
											<summary class="cursor-pointer text-xs text-muted"
												>On {limitText(saved) || 'every lane'}</summary
											>
											<div class="mt-2 flex flex-col gap-2">
												<Checklist
													legend="Lanes for {user.email}"
													choices={laneChoices}
													value={draft.lanes}
													{label}
													empty="No lanes reported by the bot yet."
													onchange={(next) => (drafts[user.id] = withLanes(draft, next))}
												/>
												<Checklist
													legend="Keys for {user.email}"
													choices={keyChoices}
													value={draft.keys}
													label={keyLabel}
													hint={LIMIT_HINT}
													empty="No casino keys in the vault."
													onchange={(next) => (drafts[user.id] = withKeys(draft, next))}
												/>
												<Button
													variant="secondary"
													disabled={sameLimit(draft, saved)}
													onclick={() =>
														changeRole(user, (user.roles[0]?.role ?? 'viewer') as Role, draft)}
													>Save lanes and keys</Button
												>
											</div>
										</details>
									{/if}
									{#if limitText(userLimit(user))}
										<span class="mt-1 block text-xs text-muted">{describe(user)}</span>
									{/if}
									{#if user.roles.some((r) => r.role === 'lane_manager')}
										{#if isOwner}
											<label class="mt-2 flex items-center gap-2 text-xs text-muted">
												Lane quota
												<input
													type="number"
													min="1"
													max="99"
													aria-label="Lane quota for {user.email}"
													value={user.lane_quota}
													onchange={(e) => setQuota(user, laneFrom(e.currentTarget.value))}
													class="min-h-11 w-20 rounded-md border border-line-strong bg-surface px-2 text-ink"
												/>
											</label>
										{:else}
											<span class="mt-1 block text-xs text-muted"
												>May run up to {lanes(user.lane_quota)}</span
											>
										{/if}
									{/if}
								{/if}
							</td>
							<td class="p-2">{user.totp_enabled ? 'On' : 'Off'}</td>
							<td class="p-2">{user.disabled ? 'Disabled' : 'Active'}</td>
							<td class="p-2 text-right">
								{#if !user.roles.some((r) => r.role === 'owner')}
									<Button
										variant={user.disabled ? 'secondary' : 'danger'}
										onclick={() => toggle(user)}
									>
										{user.disabled ? 'Enable' : 'Disable'}
									</Button>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>

	{#if pending}
		<ConfirmDialog
			bind:open={confirmOpen}
			title={pending.title}
			body={pending.body}
			confirmLabel={pending.label}
			danger={pending.danger}
			{busy}
			onconfirm={() => pending && perform(pending)}
		/>
	{/if}
	<StepUpPrompt bind:open={stepUpOpen} onverified={() => pending && perform(pending)} />
</Guard>
