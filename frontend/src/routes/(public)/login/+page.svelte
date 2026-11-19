<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api, loadMe, safeNext } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Field from '#ui/Field';
	import Notice from '#ui/Notice';

	let email = $state('');
	let password = $state('');
	let code = $state('');
	let needsCode = $state(false);
	let error: string | null = $state(null);
	let busy = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = null;
		const body = { email: email.trim(), password, code: needsCode ? code.trim() : null };
		try {
			await unwrap(api.POST('/auth/login', { body }));
			await loadMe();
			// eslint-disable-next-line svelte/no-navigation-without-resolve -- a same-site path checked by safeNext
			await goto(resolve('/(app)') + safeNext(page.url.searchParams.get('next')).slice(1), {
				replaceState: true
			});
		} catch (e) {
			if (e instanceof ApiError && e.flags.has('totp_required')) {
				needsCode = true;
			}
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Sign in · Casino dashboard</title></svelte:head>

<form onsubmit={submit} class="flex flex-col gap-4" novalidate>
	<h1 class="text-xl font-semibold">Sign in</h1>
	{#if error}<Notice tone={needsCode && !code ? 'info' : 'error'}>{error}</Notice>{/if}
	<Field label="Email" type="email" autocomplete="username" bind:value={email} required />
	<Field
		label="Password"
		type="password"
		autocomplete="current-password"
		bind:value={password}
		required
	/>
	{#if needsCode}
		<Field
			label="Authenticator code"
			inputmode="numeric"
			autocomplete="one-time-code"
			bind:value={code}
			hint="6 digits, or one of your recovery codes."
			required
		/>
	{/if}
	<Button type="submit" variant="primary" disabled={busy}>Sign in</Button>
	<a href={resolve('/(public)/reset')} class="text-sm text-accent underline"
		>Forgot your password?</a
	>
</form>
