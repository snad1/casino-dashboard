<script lang="ts">
	import Button from './Button.svelte';
	import Field from './Field.svelte';

	type Props = {
		open: boolean;
		title: string;
		body: string;
		confirmLabel: string;
		danger?: boolean;
		busy?: boolean;
		/** When set, the user must type this text before the confirm button works. */
		typed?: string | null;
		onconfirm: () => void;
		oncancel?: () => void;
	};

	let {
		open = $bindable(false),
		title,
		body,
		confirmLabel,
		danger = false,
		busy = false,
		typed = null,
		onconfirm,
		oncancel
	}: Props = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	let entered = $state('');
	const matches = $derived(!typed || entered.trim() === typed);

	$effect(() => {
		if (open && dialog && !dialog.open) {
			entered = '';
			dialog.showModal();
		}
		if (!open && dialog?.open) dialog.close();
	});

	function cancel() {
		open = false;
		oncancel?.();
	}
</script>

<dialog
	bind:this={dialog}
	onclose={() => (open = false)}
	oncancel={() => oncancel?.()}
	aria-labelledby="confirm-title"
	class="m-auto w-full max-w-md rounded-lg border border-line bg-surface p-6 text-ink backdrop:bg-canvas/80"
>
	<h2 id="confirm-title" class="text-lg font-semibold">{title}</h2>
	<p class="mt-2 text-sm text-muted">{body}</p>
	{#if typed}
		<div class="mt-4">
			<Field
				label="Type {typed} to confirm"
				bind:value={entered}
				autocomplete="off"
				autocapitalize="off"
				spellcheck="false"
			/>
		</div>
	{/if}
	<div class="mt-6 flex justify-between gap-3">
		<Button onclick={cancel}>Cancel</Button>
		<Button variant={danger ? 'danger' : 'primary'} disabled={busy || !matches} onclick={onconfirm}>
			{confirmLabel}
		</Button>
	</div>
</dialog>
