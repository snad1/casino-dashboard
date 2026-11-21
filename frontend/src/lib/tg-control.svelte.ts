import { ApiError, unwrap } from './api/client';
import { newKey } from './control';
import { api } from './session.svelte';
import { ACTION_COPY, type TgAction } from './tg';
import { toast } from './toast.svelte';

const SEND_FAILED = "Couldn't reach the bot. Try again.";

type Pending = { uid: number; action: TgAction; slot: number | null; key: string };

/**
 * Start, stop, pause and resume one Telegram user's sessions: each press opens a confirm, and
 * the confirmed press is sent once with its own idempotency key, so a retry never runs twice.
 */
class TgControl {
	pending = $state<Pending | null>(null);
	open = $state(false);
	busy = $state(false);
	/** Counts finished presses, so a page can reload what the press changed. */
	done = $state(0);

	ask(uid: number, action: TgAction, slot: number | null = null): void {
		this.pending = { uid, action, slot, key: newKey() };
		this.open = true;
	}

	get copy() {
		return this.pending ? ACTION_COPY[this.pending.action] : null;
	}

	async confirm(): Promise<void> {
		const pending = this.pending;
		if (!pending || this.busy) return;
		this.busy = true;
		try {
			await unwrap(
				api.POST('/tg/{uid}/control/{action}', {
					params: {
						path: { uid: pending.uid, action: pending.action },
						header: { 'Idempotency-Key': pending.key }
					},
					body: pending.slot === null ? {} : { slot: pending.slot }
				})
			);
			toast('success', `${ACTION_COPY[pending.action].label}: done.`);
			this.done += 1;
		} catch (e) {
			toast('error', e instanceof ApiError && e.detail ? e.detail : SEND_FAILED);
		} finally {
			this.busy = false;
			this.open = false;
			this.pending = null;
		}
	}

	cancel(): void {
		this.open = false;
		this.pending = null;
	}
}

export const tgControl = new TgControl();
