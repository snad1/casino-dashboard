<script lang="ts">
	import { control } from '#lib/control.svelte';
	import ConfirmDialog from '#ui/ConfirmDialog';
	import StepUpPrompt from '#ui/StepUpPrompt';
	import JobDrawer from './JobDrawer.svelte';

	const pending = $derived(control.pending);
</script>

<ConfirmDialog
	bind:open={control.confirmOpen}
	title={pending ? control.text(pending.action.title) : ''}
	body={pending ? control.text(pending.action.effect) : ''}
	confirmLabel={pending ? control.text(pending.action.confirm) : ''}
	danger={pending?.action.typed_confirm ?? false}
	typed={pending?.action.typed_confirm ? control.service : null}
	busy={control.sending}
	onconfirm={() => void control.confirm()}
	oncancel={() => control.cancel()}
/>
<StepUpPrompt bind:open={control.stepUpOpen} onverified={() => void control.confirm()} />
<JobDrawer />
