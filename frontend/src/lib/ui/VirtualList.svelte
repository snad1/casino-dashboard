<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';

	type Props = {
		items: T[];
		key: (item: T) => string | number;
		rowHeight: number;
		row: Snippet<[T]>;
		label: string;
		header?: Snippet;
		onend?: () => void;
	};

	let { items, key, rowHeight, row, label, header, onend }: Props = $props();

	const OVERSCAN = 6;
	let scrollTop = $state(0);
	let viewport = $state(0);

	const start = $derived(Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN));
	const end = $derived(
		Math.min(items.length, Math.ceil((scrollTop + viewport) / rowHeight) + OVERSCAN)
	);
	const visible = $derived(items.slice(start, end));

	function onscroll(event: Event) {
		const el = event.currentTarget as HTMLDivElement;
		scrollTop = el.scrollTop;
		if (onend && el.scrollTop + el.clientHeight >= items.length * rowHeight - rowHeight * 5) {
			onend();
		}
	}
</script>

<div
	role="table"
	aria-label={label}
	aria-rowcount={items.length}
	class="flex h-full min-h-0 flex-col"
>
	{#if header}{@render header()}{/if}
	<div class="min-h-0 flex-1 overflow-y-auto" {onscroll} bind:clientHeight={viewport}>
		<div role="rowgroup" class="relative" style:height="{items.length * rowHeight}px">
			<div style:transform="translateY({start * rowHeight}px)">
				{#each visible as item (key(item))}
					<div style:height="{rowHeight}px">{@render row(item)}</div>
				{/each}
			</div>
		</div>
	</div>
</div>
