<script lang="ts">
	import ActionButton from '#components/ActionButton';
	import { ApiError, unwrap } from '#lib/api/client';
	import { control } from '#lib/control.svelte';
	import { api, session } from '#lib/session.svelte';
	import { toast } from '#lib/toast.svelte';
	import { isOwner, updateText, type UpdateState } from '#lib/updates';
	import StepUpPrompt from '#ui/StepUpPrompt';

	let { service }: { service: string } = $props();

	let info = $state<UpdateState | null>(null);
	let saving = $state(false);
	let stepUpOpen = $state(false);
	let wanted: boolean | null = null;
	let handledJob: string | null = null;
	const owner = $derived(isOwner(session.me));

	async function load(svc: string) {
		const { data } = await api.GET('/{service}/update', { params: { path: { service: svc } } });
		info = data ?? { behind: null, head: null, latest: null, auto_update: false };
	}

	async function setAuto(enabled: boolean) {
		saving = true;
		try {
			info = await unwrap(
				api.PUT('/{service}/update/auto', {
					params: { path: { service } },
					body: { enabled }
				})
			);
			wanted = null;
			toast('success', enabled ? 'Auto-update is on.' : 'Auto-update is off.');
		} catch (e) {
			if (e instanceof ApiError && e.flags.has('step_up_required')) {
				wanted = enabled;
				stepUpOpen = true;
				return;
			}
			toast('error', e instanceof ApiError ? e.detail : String(e));
		} finally {
			saving = false;
		}
	}

	$effect(() => {
		info = null;
		void load(service);
	});

	$effect(() => {
		const job = control.job;
		if (!job || job.status === 'running' || handledJob === job.job_id) return;
		if (job.cmd !== `${service}.update`) return;
		handledJob = job.job_id;
		void load(service);
	});
</script>

<section class="rounded-lg border border-line bg-surface p-4" aria-labelledby="updates-title">
	<h2 id="updates-title" class="mb-2 font-semibold">Updates</h2>
	{#if info === null}
		<p class="text-sm text-muted" role="status">Checking for updates...</p>
	{:else}
		<p class="mb-3 text-sm" role="status">{updateText(info)}</p>
		{#if info.behind}
			<div class="mb-3"><ActionButton name="update" /></div>
		{/if}
		{#if owner}
			<label class="flex min-h-11 items-center gap-2 text-sm">
				<input
					type="checkbox"
					class="size-5"
					checked={info.auto_update}
					disabled={saving}
					onchange={(event) => {
						if (!info) return;
						// The box shows the saved choice until the api confirms the new one.
						event.currentTarget.checked = info.auto_update;
						void setAuto(!info.auto_update);
					}}
				/>
				Update automatically after a green CI run on main
			</label>
		{/if}
	{/if}
</section>

<StepUpPrompt
	bind:open={stepUpOpen}
	onverified={() => {
		if (wanted !== null) void setAuto(wanted);
	}}
/>
