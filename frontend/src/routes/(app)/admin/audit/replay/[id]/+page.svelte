<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import type { Terminal } from '@xterm/xterm';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api } from '#lib/session.svelte';
	import { castSize, clock, compressGaps, dueUntil, parseCast, type Cast } from '#lib/terminal';
	import { loadXterm, xtermTheme } from '#lib/xterm';
	import Button from '#ui/Button';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';
	import StepUpPrompt from '#ui/StepUpPrompt';

	const MAX_GAP_S = 2;
	const SPEEDS = [1, 2, 4];
	const BAD_FILE = 'This recording could not be read.';

	const id = $derived(page.params.id ?? '');
	let cast: Cast | null = $state(null);
	let error: string | null = $state(null);
	let loading = $state(false);
	let stepUpOpen = $state(false);
	let playing = $state(false);
	let speed = $state(1);
	let elapsed = $state(0);

	let host: HTMLDivElement | undefined = $state();
	let term: Terminal | null = null;
	let next = 0;
	let frame = 0;
	let last = 0;

	async function load() {
		loading = true;
		error = null;
		try {
			const text = await unwrap(
				api.GET('/terminal/recordings/{session_id}', {
					params: { path: { session_id: id } },
					parseAs: 'text'
				})
			);
			const parsed = parseCast(String(text));
			if (parsed === null) {
				error = BAD_FILE;
				return;
			}
			cast = { ...parsed, events: compressGaps(parsed.events, MAX_GAP_S) };
			cast.duration = cast.events.at(-1)?.at ?? 0;
			await tick();
			await start();
		} catch (e) {
			if (e instanceof ApiError && e.flags.has('step_up_required')) stepUpOpen = true;
			else error = e instanceof ApiError ? e.detail : String(e);
		} finally {
			loading = false;
		}
	}

	async function start() {
		if (!cast || !host) return;
		if (!term) {
			const { Terminal } = await loadXterm();
			term = new Terminal({
				disableStdin: true,
				cursorBlink: false,
				fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
				fontSize: 14,
				theme: xtermTheme()
			});
			term.open(host);
		}
		restart();
	}

	function restart() {
		if (!term || !cast) return;
		term.reset();
		term.resize(cast.cols, cast.rows);
		next = 0;
		elapsed = 0;
		play();
	}

	function step(now: number) {
		if (!cast || !term) return;
		elapsed += ((now - last) / 1000) * speed;
		last = now;
		const until = dueUntil(cast.events, next, elapsed);
		for (const event of cast.events.slice(next, until)) {
			if (event.kind === 'o') term.write(event.data);
			else {
				const size = castSize(event.data);
				if (size) term.resize(size.cols, size.rows);
			}
		}
		next = until;
		if (next >= cast.events.length) {
			playing = false;
			elapsed = cast.duration;
			return;
		}
		frame = requestAnimationFrame(step);
	}

	function play() {
		if (!cast) return;
		if (next >= cast.events.length) {
			restart();
			return;
		}
		playing = true;
		last = performance.now();
		frame = requestAnimationFrame(step);
	}

	function pause() {
		playing = false;
		cancelAnimationFrame(frame);
	}

	onMount(load);
	onDestroy(() => {
		cancelAnimationFrame(frame);
		term?.dispose();
	});
</script>

<svelte:head><title>Terminal replay · Casino dashboard</title></svelte:head>

<Guard perm="terminal">
	<div class="mx-auto flex max-w-6xl flex-col gap-3">
		<a class="text-sm text-accent underline" href={resolve('/(app)/admin/audit')}
			>Back to audit log</a
		>
		<h1 class="text-xl font-semibold">Terminal replay</h1>
		<p class="font-mono text-xs break-all text-muted">Session {id}</p>
		{#if error}<Notice tone="error">{error}</Notice>{/if}
		{#if loading}<p class="text-sm text-muted" role="status">Loading the recording...</p>{/if}
		{#if cast}
			<div class="flex flex-wrap items-center gap-2">
				{#if playing}
					<Button onclick={pause}>Pause</Button>
				{:else}
					<Button variant="primary" onclick={play}>Play</Button>
				{/if}
				<Button onclick={restart}>Restart</Button>
				<label class="flex items-center gap-2 text-sm">
					Speed
					<select
						bind:value={speed}
						class="min-h-11 rounded-md border border-line-strong bg-raised px-2"
					>
						{#each SPEEDS as s (s)}<option value={s}>{s}x</option>{/each}
					</select>
				</label>
				<p class="font-mono text-sm text-muted tabular-nums" aria-live="off">
					{clock(elapsed)} / {clock(cast.duration)}
				</p>
			</div>
			<p class="text-sm text-muted">Long pauses play as {MAX_GAP_S} s. Typed input is not shown.</p>
		{/if}
		<div
			bind:this={host}
			class="overflow-auto rounded-md border border-line bg-canvas p-1"
			class:hidden={!cast}
			aria-label="Recorded terminal"
		></div>
	</div>
	<StepUpPrompt bind:open={stepUpOpen} onverified={() => void load()} />
</Guard>
