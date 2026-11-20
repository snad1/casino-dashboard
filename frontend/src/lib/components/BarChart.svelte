<script lang="ts">
	/**
	 * A small bar chart for categories (hours, days, bins, lanes) that the time-axis chart
	 * library can't draw. Signed charts colour bars up or down and every value keeps its sign
	 * in the text, so colour is never the only cue; a hidden table carries the same numbers
	 * for screen readers.
	 */
	type Bar = { label: string; value: number; text: string };
	type Props = { title: string; bars: Bar[]; signed?: boolean; valueLabel: string };

	let { title, bars, signed = false, valueLabel }: Props = $props();

	const WIDTH = 600;
	const HEIGHT = 160;
	const top = $derived(Math.max(0, ...bars.map((b) => b.value)));
	const bottom = $derived(Math.min(0, ...bars.map((b) => b.value)));
	const span = $derived(top - bottom || 1);
	const zero = $derived((top / span) * HEIGHT);
	const step = $derived(bars.length ? WIDTH / bars.length : WIDTH);
	const first = $derived(bars.at(0)?.label ?? '');
	const last = $derived(bars.at(-1)?.label ?? '');
	const summary = $derived(
		bars.length ? `${title}: ${bars.length} bars from ${first} to ${last}` : `${title}: no data`
	);

	function y(value: number): number {
		return value >= 0 ? zero - (value / span) * HEIGHT : zero;
	}
	function h(value: number): number {
		return Math.max(1, (Math.abs(value) / span) * HEIGHT);
	}
	function tone(value: number): string {
		if (!signed) return 'text-accent';
		return value < 0 ? 'text-loss' : 'text-profit';
	}
</script>

<figure class="flex flex-col gap-2 rounded-lg border border-line bg-surface p-4">
	<figcaption class="text-sm font-semibold">{title}</figcaption>
	{#if bars.length}
		<svg
			viewBox="0 0 {WIDTH} {HEIGHT}"
			preserveAspectRatio="none"
			class="h-40 w-full"
			role="img"
			aria-label={summary}
		>
			{#each bars as bar, i (i)}
				<rect
					x={i * step + step * 0.1}
					y={y(bar.value)}
					width={step * 0.8}
					height={h(bar.value)}
					class="fill-current {tone(bar.value)}"
				>
					<title>{bar.label}: {bar.text}</title>
				</rect>
			{/each}
			{#if signed}
				<line x1="0" x2={WIDTH} y1={zero} y2={zero} class="stroke-current text-line-strong" />
			{/if}
		</svg>
		<div class="flex justify-between text-xs text-muted" aria-hidden="true">
			<span>{first}</span>
			<span>{last}</span>
		</div>
		<table class="sr-only">
			<caption>{title}</caption>
			<thead><tr><th scope="col">Bucket</th><th scope="col">{valueLabel}</th></tr></thead>
			<tbody>
				{#each bars as bar, i (i)}
					<tr><td>{bar.label}</td><td>{bar.text}</td></tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="text-sm text-muted">No data in this range.</p>
	{/if}
</figure>
