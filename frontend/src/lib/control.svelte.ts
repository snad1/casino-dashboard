import { ApiError, unwrap } from '#lib/api/client';
import {
	fill,
	newKey,
	type Action,
	type ActionBody,
	type ActionName,
	type ControlMenu,
	type ControlOut,
	type ControlPath,
	type Job
} from '#lib/control';
import { liveFor } from '#lib/live/index';
import { jobTopicFor } from '#lib/live/protocol';
import { api } from '#lib/session.svelte';
import { toast } from '#lib/toast.svelte';

interface Pending {
	action: Action;
	body: ActionBody;
	key: string;
	settle: (out: ControlOut | null) => void;
}

function message(e: unknown): string {
	return e instanceof ApiError ? e.detail : 'Something went wrong. Please try again.';
}

/**
 * The control flow shared by the header kill switch and the Control page: confirm, send
 * with one idempotency key, ask for a fresh code when the api wants one and retry with the
 * same key, then report with a toast and follow a job's output in the drawer.
 */
class Control {
	service: string | null = $state(null);
	menu: ControlMenu | null = $state(null);
	loadError: string | null = $state(null);
	pending: Pending | null = $state(null);
	confirmOpen = $state(false);
	stepUpOpen = $state(false);
	sending = $state(false);
	job: Job | null = $state(null);
	drawerOpen = $state(false);
	running: boolean = $derived(
		this.sending || this.job?.status === 'running' || this.menu?.running_job?.status === 'running'
	);

	#stopJob: (() => void) | null = null;

	action(name: ActionName): Action | undefined {
		return this.menu?.actions.find((a) => a.name === name);
	}

	text(template: string): string {
		return this.pending && this.service
			? fill(template, this.pending.body, this.service)
			: template;
	}

	async load(service: string): Promise<void> {
		this.service = service;
		try {
			this.menu = await unwrap(api.GET('/{service}/control', { params: { path: { service } } }));
			this.loadError = null;
			const running = this.menu.running_job;
			if (running && this.job?.job_id !== running.job_id) this.#follow(running);
		} catch (e) {
			this.loadError = message(e);
		}
	}

	/** Open the confirm for `name`; resolves with the api's answer, or null if it did not run. */
	ask(name: ActionName, body: ActionBody = {}): Promise<ControlOut | null> {
		const action = this.action(name);
		if (!action) return Promise.resolve(null);
		this.pending?.settle(null);
		return new Promise((settle) => {
			this.pending = { action, body, key: newKey(), settle };
			this.confirmOpen = true;
		});
	}

	async confirm(): Promise<void> {
		const pending = this.pending;
		const service = this.service;
		if (!pending || !service) return;
		this.sending = true;
		const label = fill(pending.action.confirm, pending.body, service);
		try {
			const out = await this.#send(service, pending);
			this.confirmOpen = false;
			this.pending = null;
			pending.settle(out);
			if (out.job_id) {
				toast('info', `${label}: started.`);
				this.#follow({
					job_id: out.job_id,
					cmd: `${service}.${pending.action.name}`,
					status: 'running',
					exit_code: null,
					duration_ms: 0,
					lines: 0
				});
			} else if (out.status === 'succeeded') {
				toast('success', `${label}: done.`);
			} else {
				toast('error', `${label} failed (exit code ${out.exit_code ?? 'unknown'}).`);
			}
			void this.load(service);
		} catch (e) {
			this.confirmOpen = false;
			if (e instanceof ApiError && e.flags.has('step_up_required')) {
				this.stepUpOpen = true;
				return;
			}
			this.pending = null;
			pending.settle(null);
			toast('error', message(e));
		} finally {
			this.sending = false;
		}
	}

	cancel(): void {
		this.pending?.settle(null);
		this.pending = null;
	}

	closeJob(): void {
		this.drawerOpen = false;
		this.#stopJob?.();
		this.#stopJob = null;
	}

	async refreshJob(): Promise<void> {
		const job = this.job;
		const service = this.service;
		if (!job || !service) return;
		try {
			this.job = await unwrap(
				api.GET('/{service}/jobs/{job_id}', {
					params: { path: { service, job_id: job.job_id } }
				})
			);
		} catch (e) {
			toast('error', message(e));
		}
		void this.load(service);
	}

	#send(service: string, pending: Pending): Promise<ControlOut> {
		const path = `/{service}/control/${pending.action.name}` as ControlPath;
		return unwrap(
			api.POST(path, {
				params: { path: { service }, header: { 'Idempotency-Key': pending.key } },
				// Each action route has its own body type; the api validates the body against it.
				body: pending.body as never
			})
		);
	}

	#follow(job: Job): void {
		const service = this.service;
		if (!service) return;
		this.#stopJob?.();
		this.job = job;
		this.drawerOpen = true;
		const live = liveFor(service);
		const topic = jobTopicFor(service, job.job_id);
		live.start(topic);
		this.#stopJob = () => live.unfollow(topic);
	}
}

export const control = new Control();
