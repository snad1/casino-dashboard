<script lang="ts">
	import { control } from '#lib/control.svelte';
	import { disabledReason, type ActionBody, type ActionName } from '#lib/control';
	import { session } from '#lib/session.svelte';
	import Button from '#ui/Button';

	let {
		name,
		body = {},
		label,
		danger = false,
		compact = false,
		busy,
		onpress,
		submit = false
	}: {
		name: ActionName;
		body?: ActionBody;
		label?: string;
		danger?: boolean;
		compact?: boolean;
		/** Replaces the service-wide busy state, for a read that only waits for itself. */
		busy?: boolean;
		/** Runs instead of the confirm modal, for a read the page handles itself. */
		onpress?: () => void;
		/** A submit button for the surrounding form, which sends the action itself. */
		submit?: boolean;
	} = $props();

	const id = $props.id();
	const action = $derived(control.action(name));
	const reason = $derived(
		action && control.service
			? disabledReason(action, session.me, control.service, busy ?? control.running)
			: null
	);
</script>

{#if action}
	<div class="flex flex-col gap-1">
		<Button
			variant={danger ? 'danger' : 'secondary'}
			disabled={reason !== null}
			aria-describedby={reason ? `${id}-reason` : undefined}
			title={compact && reason ? reason : undefined}
			type={submit ? 'submit' : 'button'}
			onclick={submit ? undefined : () => (onpress ? onpress() : void control.ask(name, body))}
		>
			{label ?? action.label}
		</Button>
		{#if reason}
			<p id="{id}-reason" class="text-xs text-muted {compact ? 'sr-only' : ''}">{reason}</p>
		{/if}
	</div>
{/if}
