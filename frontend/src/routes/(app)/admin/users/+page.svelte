<script lang="ts">
	import { onMount } from 'svelte';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api, DEFAULT_SERVICE } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import ConfirmDialog from '#ui/ConfirmDialog';
	import Field from '#ui/Field';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';
	import StepUpPrompt from '#ui/StepUpPrompt';

	type User = components['schemas']['UserOut'];
	type Role = 'admin' | 'operator' | 'viewer';
	type Pending = {
		title: string;
		body: string;
		label: string;
		danger: boolean;
		run: () => Promise<void>;
	};

	const ROLES: Role[] = ['viewer', 'operator', 'admin'];
	let users: User[] = $state([]);
	let inviteEmail = $state('');
	let inviteRole: Role = $state('viewer');
	let inviteScope = $state('all');
	let notice: { tone: 'success' | 'error'; text: string } | null = $state(null);
	let pending: Pending | null = $state(null);
	let confirmOpen = $state(false);
	let stepUpOpen = $state(false);
	let busy = $state(false);

	const describe = (user: User) =>
		user.roles
			.map(
				(r) => `${r.role}${r.service ? ` on ${r.service}` : ''}${r.lane ? ` lane ${r.lane}` : ''}`
			)
			.join(', ') || 'no role';

	async function refresh() {
		users = (await unwrap(api.GET('/admin/users'))).items;
	}

	onMount(() => {
		refresh().catch((e) => (notice = { tone: 'error', text: message(e) }));
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

	function invite(event: SubmitEvent) {
		event.preventDefault();
		const email = inviteEmail.trim();
		const service = inviteScope === 'all' ? null : inviteScope;
		ask({
			title: `Invite ${email}?`,
			body: `They get an email with a link to set a password and join as ${inviteRole}.`,
			label: 'Send invite',
			danger: false,
			run: async () => {
				await unwrap(api.POST('/auth/invites', { body: { email, role: inviteRole, service } }));
				notice = { tone: 'success', text: `Invite sent to ${email}.` };
				inviteEmail = '';
			}
		});
	}

	/** A role on every service, or (with `lane`) on that one lane of this dashboard's service. */
	function changeRole(user: User, role: Role, lane: number | null = user.roles[0]?.lane ?? null) {
		const where = lane ? `${DEFAULT_SERVICE} lane ${lane}` : 'every service';
		ask({
			title: `Make ${user.email} ${role}${lane ? ` on lane ${lane}` : ''}?`,
			body: `Their current roles (${describe(user)}) are replaced by ${role} on ${where}.`,
			label: `Make ${role}`,
			danger: false,
			run: async () => {
				await unwrap(
					api.PUT('/admin/users/{user_id}/role', {
						params: { path: { user_id: user.id } },
						body: { role, service: lane ? DEFAULT_SERVICE : null, lane }
					})
				);
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
					class="min-h-11 rounded-md border border-line-strong bg-surface px-3 text-ink"
				>
					{#each ROLES as role (role)}<option value={role}>{role}</option>{/each}
				</select>
			</label>
			<label class="flex flex-col gap-1 text-sm font-medium">
				Service
				<select
					bind:value={inviteScope}
					class="min-h-11 rounded-md border border-line-strong bg-surface px-3 text-ink"
				>
					<option value="all">All services</option>
					<option value={DEFAULT_SERVICE}>{DEFAULT_SERVICE}</option>
				</select>
			</label>
			<Button type="submit" variant="primary">Invite</Button>
		</form>

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
										{#each ROLES as role (role)}<option value={role}>{role}</option>{/each}
									</select>
									<input
										type="number"
										min="1"
										max="99"
										placeholder="all lanes"
										aria-label="Lane for {user.email}"
										value={user.roles[0]?.lane ?? ''}
										onchange={(e) =>
											changeRole(
												user,
												(user.roles[0]?.role ?? 'viewer') as Role,
												laneFrom(e.currentTarget.value)
											)}
										class="min-h-11 w-24 rounded-md border border-line-strong bg-surface px-2 text-ink"
									/>
								</div>
								{#if user.roles[0]?.lane}
									<span class="mt-1 block text-xs text-muted">{describe(user)}</span>
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
