<script lang="ts">
	import Button from './Button.svelte';

	type Props = {
		open: boolean;
		title: string;
		body: string;
		confirmLabel: string;
		danger?: boolean;
		busy?: boolean;
		onconfirm: () => void;
	};

	let {
		open = $bindable(false),
		title,
		body,
		confirmLabel,
		danger = false,
		busy = false,
		onconfirm
	}: Props = $props();

	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (open && dialog && !dialog.open) dialog.showModal();
		if (!open && dialog?.open) dialog.close();
	});
</script>

<dialog
	bind:this={dialog}
	onclose={() => (open = false)}
	aria-labelledby="confirm-title"
	class="m-auto w-full max-w-md rounded-lg border border-line bg-surface p-6 text-ink backdrop:bg-canvas/80"
>
	<h2 id="confirm-title" class="text-lg font-semibold">{title}</h2>
	<p class="mt-2 text-sm text-muted">{body}</p>
	<div class="mt-6 flex justify-between gap-3">
		<Button onclick={() => (open = false)}>Cancel</Button>
		<Button variant={danger ? 'danger' : 'primary'} disabled={busy} onclick={onconfirm}>
			{confirmLabel}
		</Button>
	</div>
</dialog>
