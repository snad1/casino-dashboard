<script lang="ts">
	import { onMount } from 'svelte';
	import type { components } from '#lib/api/schema';
	import { ApiError, unwrap } from '#lib/api/client';
	import { resolve } from '$app/paths';
	import { can } from '#lib/permissions';
	import { api, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';

	type Entry = components['schemas']['AuditOut'];
	const TERMINAL = 'WS /ws/terminal';

	function recording(entry: Entry): string | null {
		const id = entry.args.session_id;
		return entry.action === TERMINAL && entry.result === 'opened' && typeof id === 'string'
			? id
			: null;
	}

	let entries: Entry[] = $state([]);
	let next: number | null = $state(null);
	let error: string | null = $state(null);
	let busy = $state(false);

	async function more() {
		busy = true;
		try {
			const query = next === null ? { limit: 50 } : { limit: 50, before_id: next };
			const result = await unwrap(api.GET('/audit', { params: { query } }));
			entries = [...entries, ...result.items];
			next = result.next_before_id ?? null;
		} catch (e) {
			error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			busy = false;
		}
	}

	onMount(more);
</script>

<svelte:head><title>Audit log · Casino dashboard</title></svelte:head>

<Guard perm="audit.read">
	<div class="mx-auto flex max-w-5xl flex-col gap-4">
		<h1 class="text-xl font-semibold">Audit log</h1>
		{#if error}<Notice tone="error">{error}</Notice>{/if}
		<div class="overflow-x-auto">
			<table class="w-full text-left text-sm">
				<thead class="text-muted">
					<tr
						><th class="p-2">Time</th><th class="p-2">Who</th><th class="p-2">Action</th><th
							class="p-2">Result</th
						><th class="p-2">IP</th></tr
					>
				</thead>
				<tbody>
					{#each entries as entry (entry.id)}
						<tr class="border-t border-line">
							<td class="p-2 font-mono tabular-nums">{new Date(entry.ts).toLocaleString()}</td>
							<td class="p-2">{entry.actor_email ?? 'anonymous'}</td>
							<td class="p-2 font-mono">{entry.action}</td>
							<td class="p-2">
								{entry.result}
								{#if recording(entry) && can(session.me, 'terminal')}
									<a
										class="ml-2 text-accent underline"
										href={resolve('/(app)/admin/audit/replay/[id]', { id: recording(entry) ?? '' })}
										>Replay</a
									>
								{/if}
							</td>
							<td class="p-2 font-mono">{entry.ip ?? ''}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if next !== null}
			<Button onclick={more} disabled={busy}>Load more</Button>
		{/if}
	</div>
</Guard>
