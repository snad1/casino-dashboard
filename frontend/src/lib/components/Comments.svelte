<script lang="ts">
	import { onMount } from 'svelte';
	import { ApiError, unwrap } from '#lib/api/client';
	import {
		MAX_COMMENT,
		concerns,
		lengthLabel,
		listQuery,
		newComment,
		type CommentOut,
		type CommentTarget
	} from '#lib/comments';
	import { liveFor } from '#lib/live/index';
	import { can } from '#lib/permissions';
	import { api, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';

	type Props = {
		service: string;
		target: CommentTarget;
		title: string;
		at?: string | null;
		onitems?: (items: CommentOut[]) => void;
	};

	let { service, target, title, at = null, onitems }: Props = $props();

	const LOAD_FAILED = "Couldn't load comments. Try again.";
	const SAVE_FAILED = "Couldn't save the comment. Try again.";
	const uid = $props.id();

	let items: CommentOut[] = $state.raw([]);
	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let draft = $state('');
	let busy = $state(false);
	let editing = $state<number | null>(null);
	let editText = $state('');
	let confirming = $state<number | null>(null);

	const mayWrite = $derived(can(session.me, 'comment', service));
	const isChart = $derived(target.kind === 'chart');
	const needsTime = $derived(isChart && at === null);
	const tooLong = (text: string) => text.trim().length > MAX_COMMENT;
	const when = (value: string) => new Date(value).toLocaleString();
	const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback);

	async function load() {
		try {
			const result = await unwrap(
				api.GET('/{service}/comments', {
					params: { path: { service }, query: listQuery(target) }
				})
			);
			items = result.items;
			loadError = null;
			onitems?.(items);
		} catch (e) {
			loadError = message(e, LOAD_FAILED);
		}
	}

	async function post() {
		if (busy || !draft.trim() || tooLong(draft) || needsTime) return;
		busy = true;
		saveError = null;
		try {
			await unwrap(
				api.POST('/{service}/comments', {
					params: { path: { service } },
					body: newComment(target, draft, at)
				})
			);
			draft = '';
			await load();
		} catch (e) {
			saveError = message(e, SAVE_FAILED);
		} finally {
			busy = false;
		}
	}

	async function save(id: number) {
		if (busy || !editText.trim() || tooLong(editText)) return;
		busy = true;
		saveError = null;
		try {
			await unwrap(
				api.PATCH('/{service}/comments/{comment_id}', {
					params: { path: { service, comment_id: id } },
					body: { body: editText }
				})
			);
			editing = null;
			await load();
		} catch (e) {
			saveError = message(e, SAVE_FAILED);
		} finally {
			busy = false;
		}
	}

	async function remove(id: number) {
		if (busy) return;
		busy = true;
		saveError = null;
		try {
			await unwrap(
				api.DELETE('/{service}/comments/{comment_id}', {
					params: { path: { service, comment_id: id } }
				})
			);
			confirming = null;
			await load();
		} catch (e) {
			saveError = message(e, SAVE_FAILED);
		} finally {
			busy = false;
		}
	}

	$effect(() => {
		void listQuery(target);
		void load();
	});

	onMount(() =>
		liveFor(service).onComment((event) => {
			if (concerns(target, event)) void load();
		})
	);
</script>

<section aria-labelledby="{uid}-title" class="flex flex-col gap-3 text-sm">
	<h2 id="{uid}-title" class="text-base font-semibold">{title}</h2>
	{#if loadError}
		<Notice tone="error">{loadError}</Notice>
		<div><Button onclick={load}>Try again</Button></div>
	{/if}

	{#if items.length}
		<ul class="flex flex-col gap-2">
			{#each items as comment (comment.id)}
				<li class="rounded-md border border-line bg-raised p-3">
					<p class="flex flex-wrap gap-x-2 text-xs text-muted">
						<span class="font-semibold text-ink">{comment.author}</span>
						<time datetime={comment.created_at}>{when(comment.created_at)}</time>
						{#if comment.at}<span>at {when(comment.at)}</span>{/if}
						{#if comment.edited_at}<span>(edited)</span>{/if}
					</p>
					{#if editing === comment.id}
						<label class="mt-2 flex flex-col gap-1">
							<span class="sr-only">Edit comment</span>
							<textarea
								bind:value={editText}
								rows="3"
								class="rounded-md border border-line-strong bg-surface p-2 text-ink"></textarea>
							<span class="text-xs {tooLong(editText) ? 'text-loss' : 'text-muted'}"
								>{lengthLabel(editText)}</span
							>
						</label>
						<div class="mt-2 flex gap-2">
							<Button onclick={() => (editing = null)}>Cancel</Button>
							<Button
								variant="primary"
								disabled={busy || !editText.trim() || tooLong(editText)}
								onclick={() => save(comment.id)}>Save</Button
							>
						</div>
					{:else}
						<p class="mt-1 break-words whitespace-pre-wrap">{comment.body}</p>
					{/if}
					{#if confirming === comment.id}
						<div class="mt-2 flex flex-wrap items-center gap-2">
							<span>Delete this comment? This can't be undone.</span>
							<Button onclick={() => (confirming = null)}>Cancel</Button>
							<Button variant="danger" disabled={busy} onclick={() => remove(comment.id)}
								>Delete comment</Button
							>
						</div>
					{:else if editing !== comment.id && (comment.can_edit || comment.can_delete)}
						<div class="mt-2 flex gap-2">
							{#if comment.can_edit}
								<Button
									onclick={() => {
										editing = comment.id;
										editText = comment.body;
									}}>Edit</Button
								>
							{/if}
							{#if comment.can_delete}
								<Button variant="danger" onclick={() => (confirming = comment.id)}>Delete</Button>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{:else if !loadError}
		<p class="text-muted">{isChart ? 'No notes in this range.' : 'No comments yet.'}</p>
	{/if}

	{#if mayWrite}
		{#if needsTime}
			<p class="text-muted">Click the chart to add a note at that time.</p>
		{:else}
			<form
				class="flex flex-col gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					void post();
				}}
			>
				<label class="flex flex-col gap-1">
					<span>{isChart && at ? `Add a note at ${when(at)}` : 'Add a comment'}</span>
					<textarea
						bind:value={draft}
						rows="3"
						class="rounded-md border border-line-strong bg-surface p-2 text-ink"></textarea>
					<span class="text-xs {tooLong(draft) ? 'text-loss' : 'text-muted'}"
						>{lengthLabel(draft)}</span
					>
				</label>
				<div>
					<Button type="submit" variant="primary" disabled={busy || !draft.trim() || tooLong(draft)}
						>Post</Button
					>
				</div>
			</form>
		{/if}
	{/if}
	<div aria-live="polite">
		{#if saveError}<Notice tone="error">{saveError}</Notice>{/if}
	</div>
</section>
