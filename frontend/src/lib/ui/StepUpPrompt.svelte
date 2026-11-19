<script lang="ts">
	import { ApiError, unwrap } from '#lib/api/client';
	import { api } from '#lib/session.svelte';
	import Button from './Button.svelte';
	import Field from './Field.svelte';

	let { open = $bindable(false), onverified }: { open: boolean; onverified: () => void } = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	let code = $state('');
	let error: string | null = $state(null);
	let busy = $state(false);

	$effect(() => {
		if (open && dialog && !dialog.open) dialog.showModal();
		if (!open && dialog?.open) dialog.close();
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = null;
		try {
			await unwrap(api.POST('/auth/step-up', { body: { code: code.trim() } }));
			code = '';
			open = false;
			onverified();
		} catch (e) {
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			busy = false;
		}
	}
</script>

<dialog
	bind:this={dialog}
	onclose={() => (open = false)}
	aria-labelledby="step-up-title"
	class="m-auto w-full max-w-sm rounded-lg border border-line bg-surface p-6 text-ink backdrop:bg-canvas/80"
>
	<form onsubmit={submit} class="flex flex-col gap-4">
		<h2 id="step-up-title" class="text-lg font-semibold">Confirm it's you</h2>
		<p class="text-sm text-muted">Enter the code from your authenticator app to continue.</p>
		<Field
			label="Authenticator code"
			bind:value={code}
			inputmode="numeric"
			autocomplete="one-time-code"
			required
			{error}
		/>
		<div class="flex justify-between gap-3">
			<Button onclick={() => (open = false)}>Cancel</Button>
			<Button type="submit" variant="primary" disabled={busy}>Confirm</Button>
		</div>
	</form>
</dialog>
