<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ActionButton from '#components/ActionButton';
	import { ApiError, unwrap } from '#lib/api/client';
	import { pendingText, type ConfigOut } from '#lib/config';
	import type { ActionBody, ActionName } from '#lib/control';
	import { control } from '#lib/control.svelte';
	import { liveFor } from '#lib/live/index';
	import { can } from '#lib/permissions';
	import { problems } from '#lib/problems';
	import { api, session } from '#lib/session.svelte';
	import Button from '#ui/Button';
	import Notice from '#ui/Notice';

	const service = $derived(page.params.service ?? '');
	const live = $derived(liveFor(service));
	const lanes = $derived(
		Object.keys(live.lanes)
			.map(Number)
			.sort((a, b) => a - b)
	);
	const issues = $derived(problems(live.lanes, live.system?.agent));
	let days = $state(7);
	let report: { title: string; text: string } | null = $state(null);
	let checking = $state(false);

	const units = [{ group: 'tg', name: 'Telegram bot' }] as const;

	async function check(name: ActionName, title: string) {
		checking = true;
		const out = await control.run(name);
		checking = false;
		if (out) report = { title, text: out.output ?? '' };
	}
	let branch = $state('');
	let config = $state<ConfigOut | null>(null);
	let configError = $state<string | null>(null);
	let handledJob: string | null = null;

	/** While a lane still waits for its session end, how often to look again. */
	const ROLLOUT_POLL_MS = 30_000;
	const LANE_CODE_JOBS = ['update', 'restart', 'lane_restart'];
	const startLane = (lane: number) => void control.ask('lane_start', { lane });
	const waiting = $derived(
		Object.values(config?.rollout ?? {}).includes('waiting for session end')
	);

	async function loadConfig() {
		try {
			config = await unwrap(api.GET('/{service}/config', { params: { path: { service } } }));
			configError = null;
		} catch (e) {
			configError = e instanceof ApiError ? e.detail : String(e);
		}
	}

	/** Restart one lane at its session end so it runs on the saved settings; the other lanes
	 * keep betting. Then show what is still waiting. */
	async function applyPending(lane: number) {
		const out = await control.ask('lane_restart_end', { lane });
		if (out?.status === 'succeeded') await loadConfig();
	}

	$effect(() => {
		if (service && can(session.me, 'config.write', service)) void loadConfig();
	});

	$effect(() => {
		const job = control.job;
		if (!job || job.status === 'running' || handledJob === job.job_id) return;
		if (!LANE_CODE_JOBS.some((name) => job.cmd === `${service}.${name}`)) return;
		handledJob = job.job_id;
		void loadConfig();
	});

	$effect(() => {
		if (!waiting) return;
		const timer = setInterval(() => void loadConfig(), ROLLOUT_POLL_MS);
		return () => clearInterval(timer);
	});

	const updateBody = $derived.by(() => {
		const body: ActionBody = {};
		if (branch.trim()) body.branch = branch.trim();
		return body;
	});

	const section = 'rounded-lg border border-line bg-surface p-4';
	const row = 'flex flex-wrap items-start gap-3';
	const input =
		'min-h-11 rounded-md border border-line-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus';
</script>

<svelte:head><title>Control · Casino dashboard</title></svelte:head>

<div class="mx-auto flex max-w-4xl flex-col gap-4">
	<h1 class="text-xl font-semibold">Control</h1>

	{#if control.loadError}
		<Notice tone="error">{control.loadError}</Notice>
	{:else if !control.menu}
		<p class="text-sm text-muted" role="status">Loading controls...</p>
	{:else}
		{#if control.menu.running_job}
			<Notice>
				{control.menu.running_job.cmd} is running.
				<button type="button" class="underline" onclick={() => (control.drawerOpen = true)}
					>Show output</button
				>
			</Notice>
		{/if}

		<section class={section} aria-labelledby="problems-title">
			<h2 id="problems-title" class="mb-3 font-semibold">Problems</h2>
			{#if issues.length === 0}
				<p class="text-sm text-muted">No problems.</p>
			{:else}
				<ul class="flex flex-col gap-3" role="alert">
					{#each issues as issue (issue.text)}
						<li class={row}>
							<span class="pt-3 text-sm text-warn">{issue.text}</span>
							{#if issue.lane !== undefined}
								{@const lane = issue.lane}
								<Button onclick={() => startLane(lane)}>Start lane {lane}</Button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<section class={section} aria-labelledby="lanes-title">
			<h2 id="lanes-title" class="mb-3 font-semibold">Lanes</h2>
			{#if configError}
				<div class="mb-3"><Notice tone="error">{configError}</Notice></div>
			{/if}
			<ul class="flex flex-col gap-3">
				<li class={row}>
					<span class="min-w-20 pt-3 text-sm font-medium">All lanes</span>
					<ActionButton name="pause" body={{ target: 'all' }} />
					<ActionButton name="resume" body={{ target: 'all' }} />
					<ActionButton name="reload" body={{ target: 'all' }} />
				</li>
				{#each lanes as lane (lane)}
					{@const code = config?.rollout?.[String(lane)]}
					{@const pending = config?.pending?.[String(lane)] ?? []}
					<li class={row}>
						<span class="min-w-20 pt-3 text-sm">
							Lane {lane}
							<span class="block text-xs text-muted">{live.lanes[lane]?.status ?? 'unknown'}</span>
						</span>
						<ActionButton name="pause" body={{ target: lane }} />
						<ActionButton name="resume" body={{ target: lane }} />
						<ActionButton name="reload" body={{ target: lane }} />
						<ActionButton name="lane_restart_end" body={{ lane }} />
						<ActionButton name="lane_restart" body={{ lane }} />
						{#if code}
							<p class="w-full text-sm {code === 'new' ? 'text-muted' : 'text-warn'}">
								Code: {code}
							</p>
						{/if}
						{#if pending.length}
							<div class="flex w-full flex-wrap items-center gap-3">
								<p class="text-sm text-warn">Waiting for a restart: {pendingText(pending)}</p>
								<Button onclick={() => void applyPending(lane)}>Apply at session end</Button>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		</section>

		<section class={section} aria-labelledby="count-title">
			<h2 id="count-title" class="mb-2 font-semibold">Lanes count</h2>
			<p class="text-sm">
				<a class="underline" href={resolve('/(app)/[service=service]/lanes', { service })}
					>Change lanes and split</a
				>
				<span class="text-muted">with a preview of each lane's share and which lanes stop.</span>
			</p>
		</section>

		<section class={section} aria-labelledby="service-title">
			<h2 id="service-title" class="mb-3 font-semibold">Service</h2>
			<div class={row}>
				<ActionButton name="start" />
				<ActionButton name="restart_rolling" />
				<ActionButton name="stop" />
			</div>
		</section>

		<section class={section} aria-labelledby="services-title">
			<h2 id="services-title" class="font-semibold">Services</h2>
			<p class="mb-3 text-sm text-muted">The Telegram bot. Betting is not touched.</p>
			<ul class="flex flex-col gap-3">
				{#each units as unit (unit.group)}
					<li class={row}>
						<span class="min-w-32 pt-3 text-sm font-medium">{unit.name}</span>
						<ActionButton
							name="{unit.group}_status"
							busy={checking}
							onpress={() => void check(`${unit.group}_status`, unit.name)}
						/>
						<ActionButton name="{unit.group}_start" />
						<ActionButton name="{unit.group}_stop" />
						<ActionButton name="{unit.group}_restart" />
					</li>
				{/each}
				<li class={row}>
					<span class="min-w-32 pt-3 text-sm font-medium">All services</span>
					<ActionButton
						name="status_all"
						busy={checking}
						onpress={() => void check('status_all', 'All services')}
					/>
					<ActionButton name="start_all" />
				</li>
			</ul>
			{#if report}
				<pre
					class="mt-3 max-h-96 overflow-auto rounded-md bg-raised p-3 font-mono text-xs whitespace-pre-wrap"
					aria-label="{report.title} status">{report.text || 'No output.'}</pre>
			{/if}
		</section>

		<section class="{section} border-loss" aria-labelledby="danger-title">
			<h2 id="danger-title" class="font-semibold text-loss">Danger zone</h2>
			<p class="mb-3 text-sm text-muted">
				These need an admin, a fresh authenticator code and the service name typed out.
			</p>
			<div class="flex flex-col gap-4">
				<div class={row}>
					<ActionButton name="run_reset" danger />
					<ActionButton name="reset" danger />
					<ActionButton name="doctor_fix" danger />
					<ActionButton name="stop_all" danger />
				</div>
				<div class={row}>
					<label class="flex flex-col gap-1 text-sm font-medium">
						Older than (days)
						<input type="number" min="1" max="30" class={input} bind:value={days} />
					</label>
					<div class="pt-6"><ActionButton name="cleanup" body={{ days }} danger /></div>
				</div>
				<div class={row}>
					<label class="flex flex-col gap-1 text-sm font-medium">
						Branch (blank keeps the current one)
						<input type="text" class={input} bind:value={branch} autocomplete="off" />
					</label>
					<div class="pt-6"><ActionButton name="update" body={updateBody} danger /></div>
				</div>
			</div>
		</section>
	{/if}
</div>
