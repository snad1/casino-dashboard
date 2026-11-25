<script lang="ts">
	import { BASE, badgeClass, costLabel, display, type Preview } from '#lib/config';
	import { laneLabel } from '#lib/lanes';
	import type { Lanes } from '#lib/live/protocol';

	/** With `costs`, each row says what it restarts; otherwise only live or restart. `lanes`
	 * names the lanes that have their own name. */
	let {
		preview,
		costs = false,
		lanes = {}
	}: { preview: Preview; costs?: boolean; lanes?: Lanes } = $props();
</script>

<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="diff-title">
	<h2 id="diff-title" class="mb-3 font-semibold">Changes</h2>
	<table class="w-full text-left text-sm">
		<thead class="text-muted">
			<tr><th>Scope</th><th>Key</th><th>Old</th><th>New</th><th>Applies</th></tr>
		</thead>
		<tbody>
			{#each [['base', preview.base] as const, ...Object.entries(preview.lanes)] as [where, rows] (where)}
				{#each rows as row (row.key)}
					<tr class="border-t border-line">
						<td class="py-2">{where === 'base' ? 'Base' : laneLabel(Number(where), lanes)}</td>
						<td>{row.key}</td>
						<td class="font-mono">{display(row.old)}</td>
						<td class="font-mono">{row.removed ? 'base value' : display(row.new)}</td>
						<td>
							<span class={badgeClass(row.apply)}
								>{costs
									? costLabel(row.apply, where === 'base' ? BASE : where)
									: row.apply === 'live'
										? 'live'
										: 'restart required'}</span
							>
						</td>
					</tr>
				{/each}
			{/each}
		</tbody>
	</table>
	{#if preview.warnings.length}
		<ul class="mt-3 list-disc pl-5 text-sm text-warn">
			{#each preview.warnings as warning, i (i)}<li>{warning}</li>{/each}
		</ul>
	{/if}
</section>
