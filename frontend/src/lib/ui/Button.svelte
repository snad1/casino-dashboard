<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	type Props = HTMLButtonAttributes & {
		variant?: 'primary' | 'secondary' | 'danger';
		children: Snippet;
	};

	let { variant = 'secondary', type = 'button', children, ...rest }: Props = $props();

	const looks = {
		primary: 'bg-accent text-on-accent',
		secondary: 'bg-raised text-ink border border-line-strong',
		danger: 'bg-raised text-loss border border-loss'
	};
</script>

<button
	{type}
	class="inline-flex min-h-11 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:opacity-60 {looks[
		variant
	]}"
	{...rest}
>
	{@render children()}
</button>
