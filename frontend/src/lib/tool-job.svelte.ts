import { unwrap } from '#lib/api/client';
import { addLine, type ActionBody, type ActionName, type Job } from '#lib/control';
import { control } from '#lib/control.svelte';
import type { JobLine } from '#lib/live/protocol';
import { api } from '#lib/session.svelte';

/**
 * One tools-page job shown inline: start it without a modal, collect its output lines
 * from the live socket, and read its final status when it ends. The page calls `dispose`
 * when it goes away; with `cancelOnLeave` (a log follow) a running job is cancelled too,
 * while a backtest or a doctor run keeps going on the host.
 */
export class ToolJob {
	job: Job | null = $state(null);
	lines: JobLine[] = $state.raw([]);
	starting = $state(false);
	running: boolean = $derived(this.starting || this.job?.status === 'running');

	#stopWatch: (() => void) | null = null;

	constructor(readonly cancelOnLeave = false) {}

	async start(name: ActionName, body: ActionBody = {}): Promise<void> {
		this.#unwatch();
		this.lines = [];
		this.job = null;
		this.starting = true;
		// An action that must be confirmed (a real bet) opens the confirm dialog first.
		const confirm = control.action(name)?.confirm_first === true;
		const out = await (confirm ? control.ask(name, body) : control.run(name, body));
		this.starting = false;
		const jobId = out?.job_id;
		if (!jobId || !control.service) return;
		this.job = {
			job_id: jobId,
			cmd: `${control.service}.${name}`,
			status: 'running',
			exit_code: null,
			duration_ms: 0,
			lines: 0
		};
		this.#stopWatch = control.watch(jobId, (message) => {
			if (message.op === 'job') this.lines = addLine(this.lines, message);
			else void this.#finish(jobId);
		});
	}

	/** Cancel the running job; `keepalive` lets the request finish while the page closes. */
	async cancel(keepalive = false): Promise<void> {
		const job = this.job;
		this.#unwatch();
		if (job?.status === 'running') {
			await control.stopJob(job.job_id, keepalive);
			if (!keepalive) await this.#finish(job.job_id);
		}
	}

	dispose(): void {
		if (this.cancelOnLeave && this.job?.status === 'running') void this.cancel(true);
		this.#unwatch();
	}

	async #finish(jobId: string): Promise<void> {
		this.#unwatch();
		const service = control.service;
		if (!service) return;
		try {
			this.job = await unwrap(
				api.GET('/{service}/jobs/{job_id}', { params: { path: { service, job_id: jobId } } })
			);
		} catch {
			if (this.job) this.job = { ...this.job, status: 'failed' };
		}
	}

	#unwatch(): void {
		this.#stopWatch?.();
		this.#stopWatch = null;
	}
}
