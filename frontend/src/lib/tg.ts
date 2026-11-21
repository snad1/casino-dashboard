import { can, type Me } from './permissions';

/** The Telegram bot's service name, whose live topics are one per user. */
export const TG_SERVICE = 'wolfbet-tg';
export const FORBIDDEN = 'You do not have permission to do that.';

export type TgAction = 'start' | 'stop' | 'pause' | 'resume';

/** Where a Telegram account lives in the app; null for any other account. */
export function tgHome(me: Me | null): string | null {
	return me?.tg_uid != null ? `/tg/${me.tg_uid}` : null;
}

/**
 * Whether `me` may open Telegram user `uid`'s pages: their own account, or whoever manages
 * users. The api decides; this only avoids asking for what it would refuse.
 */
export function canSeeTgUser(me: Me | null, uid: number): boolean {
	if (me === null) return false;
	if (me.tg_uid != null) return me.tg_uid === uid;
	return can(me, 'users.manage');
}

export function sessionsCsvUrl(uid: number): string {
	return `/api/tg/${uid}/sessions.csv`;
}

export function betsCsvUrl(uid: number, epoch: number, id: number): string {
	return `/api/tg/${uid}/sessions/${epoch}/${id}/bets.csv`;
}

const LABELS: Record<string, string> = {
	currency: 'Currency',
	game: 'Game',
	strategy: 'Strategy',
	strategy_key: 'Strategy key',
	pipeline: 'Pipeline',
	rule: 'Rule',
	multiplier: 'Multiplier',
	bet_value: 'Bet value',
	base_bet: 'Base bet',
	win_mult: 'On win, multiply by',
	loss_mult: 'On loss, multiply by',
	bet_delay: 'Delay between bets (s)',
	max_profit: 'Stop at profit',
	max_loss: 'Stop at loss',
	max_bets: 'Stop after bets',
	max_wins: 'Stop after wins',
	stop_on_balance: 'Stop below balance',
	purge_days: 'Keep history (days)',
	custom_rules_text: 'Custom rules'
};

/** A saved setting's label: a known name, or the key with underscores as spaces. */
export function settingLabel(key: string): string {
	const label = LABELS[key] ?? key.replaceAll('_', ' ');
	return label.charAt(0).toUpperCase() + label.slice(1);
}

/** A saved setting as text; empty values read as "Not set". */
export function settingValue(value: unknown): string {
	if (value === null || value === undefined || value === '') return 'Not set';
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

export const ACTION_COPY: Record<TgAction, { label: string; title: string; body: string }> = {
	start: {
		label: 'Start',
		title: 'Start a session?',
		body: 'The bot starts betting with your saved settings, as /bet does.'
	},
	pause: {
		label: 'Pause',
		title: 'Pause this session?',
		body: 'Betting stops until you resume. The session stays open.'
	},
	resume: {
		label: 'Resume',
		title: 'Resume this session?',
		body: 'Betting starts again where it paused.'
	},
	stop: {
		label: 'Stop',
		title: 'Stop this session?',
		body: 'Betting stops and the session is closed, as /stop does.'
	}
};
