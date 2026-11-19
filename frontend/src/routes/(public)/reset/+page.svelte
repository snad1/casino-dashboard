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
	let email = $state('');
	let password = $state('');
	let message: string | null = $state(null);
	let error: string | null = $state(null);
	let busy = $state(false);

	onMount(() => {
		token = tokenFromHash();
	});

	async function run(action: () => Promise<string>) {
		busy = true;
		error = null;
		try {
			message = await action();
		} catch (e) {
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			busy = false;
		}
	}

	function request(event: SubmitEvent) {
		event.preventDefault();
		void run(async () => {
			const sent = await unwrap(
				api.POST('/auth/password-reset', { body: { email: email.trim() } })
			);
			return sent.detail;
		});
	}

	function confirm(event: SubmitEvent) {
		event.preventDefault();
		void run(async () => {
			await unwrap(
				api.POST('/auth/password-reset/confirm', { body: { token: token ?? '', password } })
			);
			return 'Your password is changed. You can sign in now.';
		});
	}
</script>

<svelte:head><title>Reset password · Casino dashboard</title></svelte:head>

<h1 class="mb-4 text-xl font-semibold">Reset your password</h1>
{#if message}
	<Notice tone="success">{message}</Notice>
	<a href={resolve('/(public)/login')} class="mt-4 inline-block text-accent underline">Sign in</a>
{:else if token === null}
	<form onsubmit={request} class="flex flex-col gap-4">
		{#if error}<Notice tone="error">{error}</Notice>{/if}
		<Field label="Email" type="email" autocomplete="username" bind:value={email} required />
		<Button type="submit" variant="primary" disabled={busy}>Send reset link</Button>
	</form>
{:else}
	<form onsubmit={confirm} class="flex flex-col gap-4">
		{#if error}<Notice tone="error">{error}</Notice>{/if}
		<Field
			label="New password"
			type="password"
			autocomplete="new-password"
			hint="At least 12 characters."
			bind:value={password}
			required
		/>
		<Button type="submit" variant="primary" disabled={busy}>Set password</Button>
	</form>
{/if}
