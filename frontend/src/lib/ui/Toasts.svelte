<script lang="ts">
	import { dismiss, toasts } from '#lib/toast.svelte';
	import Notice from './Notice.svelte';
</script>

<section
	aria-label="Notifications"
	class="pointer-events-none fixed inset-x-4 bottom-20 z-30 flex flex-col gap-2 md:inset-x-auto md:right-4 md:bottom-4 md:w-96"
>
	{#each toasts.items as item (item.id)}
		<div class="pointer-events-auto flex items-start gap-2 rounded-md bg-surface shadow-lg">
			<div class="flex-1">
				<Notice tone={item.tone}>{item.text}</Notice>
			</div>
			<button
				type="button"
				onclick={() => dismiss(item.id)}
				class="min-h-11 min-w-11 text-sm text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-focus"
				aria-label="Dismiss">×</button
			>
		</div>
	{/each}
</section>
