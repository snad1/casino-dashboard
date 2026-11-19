<script lang="ts">
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api } from '#lib/session.svelte';
	import { tokenFromHash } from '#lib/token';
	import Button from '#ui/Button';
	import Field from '#ui/Field';
	import Notice from '#ui/Notice';

	let token: string | null = $state(null);
	let password = $state('');
	let repeat = $state('');
	let error: string | null = $state(null);
	let done = $state(false);
	let busy = $state(false);

	onMount(() => {
		token = tokenFromHash();
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		error = null;
		if (password !== repeat) {
			error = 'The two passwords are different.';
			return;
		}
		busy = true;
		try {
			await unwrap(api.POST('/auth/invites/accept', { body: { token: token ?? '', password } }));
			done = true;
		} catch (e) {
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Accept invite · Casino dashboard</title></svelte:head>

<h1 class="mb-4 text-xl font-semibold">Accept your invite</h1>
{#if done}
	<Notice tone="success">Account created. You can sign in now.</Notice>
	<a href={resolve('/(public)/login')} class="mt-4 inline-block text-accent underline">Sign in</a>
{:else if token === null}
	<Notice tone="error">This invite link is incomplete. Ask for a new invite.</Notice>
{:else}
	<form onsubmit={submit} class="flex flex-col gap-4">
		{#if error}<Notice tone="error">{error}</Notice>{/if}
		<Field
			label="Choose a password"
			type="password"
			autocomplete="new-password"
			hint="At least 12 characters; a long phrase of unrelated words works well."
			bind:value={password}
			required
		/>
		<Field
			label="Repeat the password"
			type="password"
			autocomplete="new-password"
			bind:value={repeat}
			required
		/>
		<Button type="submit" variant="primary" disabled={busy}>Create account</Button>
	</form>
{/if}
