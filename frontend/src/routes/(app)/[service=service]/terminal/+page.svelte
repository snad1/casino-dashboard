<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import type { FitAddon } from '@xterm/addon-fit';
	import type { SearchAddon } from '@xterm/addon-search';
	import type { Terminal } from '@xterm/xterm';
	import { ApiError, unwrap } from '#lib/api/client';
	import { api } from '#lib/session.svelte';
	import {
		clock,
		closeText,
		ctrl,
		decodeFrame,
		inputFrames,
		KEYS,
		resizeFrame,
		safeLink,
		terminalSocketUrl,
		type KeyName
	} from '#lib/terminal';
	import { loadXterm, xtermTheme } from '#lib/xterm';
	import Button from '#ui/Button';
	import Guard from '#ui/Guard';
	import Notice from '#ui/Notice';
	import StepUpPrompt from '#ui/StepUpPrompt';

	type State = 'idle' | 'opening' | 'open' | 'closed';
	const BAR: KeyName[] = ['Esc', 'Tab', 'Up', 'Down', 'Left', 'Right'];
	const ARROWS: Partial<Record<KeyName, string>> = {
		Up: '↑',
		Down: '↓',
		Left: '←',
		Right: '→'
	};

	let status: State = $state('idle');
	let error: string | null = $state(null);
	let closedText = $state('');
	let stepUpOpen = $state(false);
	let ctrlArmed = $state(false);
	let find = $state('');
	let idleLeft = $state(0);
	let maxLeft = $state(0);

	let host: HTMLDivElement | undefined = $state();
	let term: Terminal | null = null;
	let fit: FitAddon | null = null;
	let search: SearchAddon | null = null;
	let socket: WebSocket | null = null;
	let observer: ResizeObserver | null = null;
	let timer: ReturnType<typeof setInterval> | undefined;
	let resizeDebounce: ReturnType<typeof setTimeout> | undefined;
	let limits = { idleS: 0, maxS: 0, openedAt: 0, lastInput: 0 };
	let endReason: string | null = null;

	function send(frame: Uint8Array<ArrayBuffer>) {
		if (socket?.readyState === WebSocket.OPEN) socket.send(frame);
	}

	function type(data: string) {
		limits.lastInput = Date.now();
		for (const frame of inputFrames(data)) send(frame);
	}

	function sendSize() {
		if (term) send(resizeFrame(term.cols, term.rows));
	}

	async function ensureTerminal() {
		if (term || !host) return;
		const { Terminal, FitAddon, SearchAddon, WebLinksAddon } = await loadXterm();
		term = new Terminal({
			cursorBlink: true,
			fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
			fontSize: 14,
			scrollback: 5000,
			theme: xtermTheme()
		});
		fit = new FitAddon();
		search = new SearchAddon();
		term.loadAddon(fit);
		term.loadAddon(search);
		term.loadAddon(
			new WebLinksAddon((_event, uri) => {
				const url = safeLink(uri);
				if (url) window.open(url, '_blank', 'noopener,noreferrer');
			})
		);
		term.open(host);
		fit.fit();
		term.onData((data) => {
			if (ctrlArmed && data.length === 1) {
				ctrlArmed = false;
				type(ctrl(data) ?? data);
				return;
			}
			type(data);
		});
		term.onBinary((data) => type(data));
		observer = new ResizeObserver(() => {
			clearTimeout(resizeDebounce);
			resizeDebounce = setTimeout(() => {
				fit?.fit();
				sendSize();
			}, 100);
		});
		observer.observe(host);
	}

	async function open() {
		error = null;
		status = 'opening';
		let ticket: string;
		try {
			ticket = (await unwrap(api.POST('/terminal/ticket'))).ticket;
		} catch (e) {
			status = 'idle';
			if (e instanceof ApiError && e.flags.has('step_up_required')) {
				stepUpOpen = true;
				return;
			}
			error = e instanceof ApiError ? e.detail : String(e);
			return;
		}
		await tick();
		await ensureTerminal();
		if (!term) return;
		term.reset();
		endReason = null;
		const ws = new WebSocket(terminalSocketUrl(ticket, term.cols, term.rows));
		ws.binaryType = 'arraybuffer';
		socket = ws;
		ws.onmessage = (event: MessageEvent<ArrayBuffer>) => {
			const message = decodeFrame(event.data);
			if (message === null) return;
			if (message.kind === 'output') term?.write(message.data);
			else if (message.kind === 'end') endReason = message.reason;
			else {
				status = 'open';
				limits = {
					idleS: message.idleS,
					maxS: message.maxS,
					openedAt: Date.now(),
					lastInput: Date.now()
				};
				countdown();
				timer = setInterval(countdown, 1000);
				term?.focus();
			}
		};
		ws.onclose = () => {
			clearInterval(timer);
			if (socket === ws) socket = null;
			const opened = status === 'open';
			status = 'closed';
			closedText = closeText(endReason ?? (opened ? 'closed' : null));
		};
	}

	function countdown() {
		const now = Date.now();
		idleLeft = limits.idleS - (now - limits.lastInput) / 1000;
		maxLeft = limits.maxS - (now - limits.openedAt) / 1000;
	}

	function close() {
		endReason = 'closed';
		socket?.close(1000);
	}

	function press(key: KeyName) {
		type(KEYS[key]);
		term?.focus();
	}

	function findNext(event: SubmitEvent) {
		event.preventDefault();
		if (find) search?.findNext(find);
	}

	onDestroy(() => {
		clearInterval(timer);
		clearTimeout(resizeDebounce);
		observer?.disconnect();
		socket?.close(1000);
		term?.dispose();
	});
</script>

<svelte:head><title>Terminal · Casino dashboard</title></svelte:head>

<Guard perm="terminal">
	<div class="flex h-full min-h-[70vh] flex-col gap-3">
		<header class="flex flex-wrap items-center gap-3">
			<h1 class="text-xl font-semibold">Terminal</h1>
			{#if status === 'open'}
				<p class="text-sm text-warn" role="status">This session is recorded.</p>
				<p class="font-mono text-sm text-muted tabular-nums">
					Idle closes in {clock(idleLeft)} · Session ends in {clock(maxLeft)}
				</p>
				<form class="ml-auto flex items-center gap-2" onsubmit={findNext}>
					<label for="term-find" class="sr-only">Find in output</label>
					<input
						id="term-find"
						bind:value={find}
						placeholder="Find in output"
						class="min-h-11 w-40 rounded-md border border-line-strong bg-raised px-3 text-sm"
					/>
					<Button type="submit">Find</Button>
				</form>
				<Button variant="danger" onclick={close}>Close</Button>
			{:else}
				<Button variant="primary" onclick={open} disabled={status === 'opening'}>
					{status === 'closed' ? 'Open again' : 'Open terminal'}
				</Button>
			{/if}
		</header>

		{#if error}<Notice tone="error">{error}</Notice>{/if}
		{#if status === 'idle' && !error}
			<Notice
				>Opens a shell on the host. It needs your authenticator code, is recorded, and the owner is
				emailed each time it opens.</Notice
			>
		{/if}
		{#if status === 'closed'}<Notice>{closedText}</Notice>{/if}

		<div
			bind:this={host}
			class="min-h-[50vh] flex-1 overflow-hidden rounded-md border border-line bg-canvas p-1"
			class:hidden={status === 'idle'}
			aria-label="Terminal"
		></div>

		{#if status === 'open'}
			<div class="flex flex-wrap gap-2 md:hidden" role="group" aria-label="Terminal keys">
				<Button aria-pressed={ctrlArmed} onclick={() => (ctrlArmed = !ctrlArmed)}>Ctrl</Button>
				{#each BAR as key (key)}
					<Button aria-label={key} onclick={() => press(key)}>{ARROWS[key] ?? key}</Button>
				{/each}
			</div>
		{/if}
	</div>
	<StepUpPrompt bind:open={stepUpOpen} onverified={() => void open()} />
</Guard>
