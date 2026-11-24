import { plainText } from '#lib/format';
import type { components } from '#lib/api/schema';
import type { Lanes } from '#lib/live/protocol';

export type ConfigKey = components['schemas']['ConfigKey'];
export type ConfigOut = components['schemas']['ConfigOut'];
export type ConfigChange = components['schemas']['ConfigChange'];
export type Preview = components['schemas']['PreviewOut'];
export type DiffRow = components['schemas']['DiffRow'];
export type FieldKind = 'select' | 'boolean' | 'number' | 'text' | 'long';
/** `base`, or a lane number as text ("2"), the way the api keys lane overrides. */
export type Scope = string;
export const BASE: Scope = 'base';
export const LANE_SPLIT = 'lane_split';
export const RELOAD_WAIT_MS = 10_000;

/** One field as the user left it; `useBase` only applies on a lane tab. */
export interface Edit {
	value: string;
	useBase: boolean;
}
export type Edits = Record<Scope, Record<string, Edit>>;

export function fieldKind(key: ConfigKey): FieldKind {
	if (key.choices?.length) return 'select';
	if (key.type === 'boolean') return 'boolean';
	if (key.type === 'number' || key.type === 'integer') return 'number';
	if (key.format === 'rules') return 'long';
	return 'text';
}

/** A saved value as form text: lists read `N:X`, nothing reads empty. */
export function display(value: unknown): string {
	if (value === null || value === undefined) return '';
	if (Array.isArray(value)) return value.map(plainText).join(':');
	return plainText(value);
}

/** A lane's settings waiting for a restart, as `key running -> saved, ...`. */
export function pendingText(rows: ConfigOut['pending'][string]): string {
	return rows.map((p) => `${p.key} ${display(p.running)} -> ${display(p.saved)}`).join(', ');
}

export function overridden(config: ConfigOut, scope: Scope, key: string): boolean {
	return scope !== BASE && key in (config.lanes[scope] ?? {});
}

/** What `key` currently is in `scope`: a lane without an override reads the base value. */
export function current(config: ConfigOut, scope: Scope, key: string): string {
	if (key === LANE_SPLIT) return config.lane_split;
	if (overridden(config, scope, key)) return display(config.lanes[scope]?.[key]);
	return display(config.base[key]);
}

/** The form's starting state for `scope`, one entry per key shown there. */
export function initialEdits(config: ConfigOut, scope: Scope): Record<string, Edit> {
	const keys = config.keys.filter((k) => scope === BASE || k.lane_overridable).map((k) => k.key);
	if (scope === BASE) keys.push(LANE_SPLIT);
	return Object.fromEntries(
		keys.map((key) => [
			key,
			{ value: current(config, scope, key), useBase: !overridden(config, scope, key) }
		])
	);
}

/**
 * The edits that differ from what is saved, as `{key, value, lane}` for the api, which builds
 * the bot's `key=value` text itself. An emptied field is "off" when the key allows it, and
 * ticking "Use base" on an overridden lane key sends "default" to drop the override.
 */
export function changesFrom(config: ConfigOut, edits: Edits): ConfigChange[] {
	const offAllowed = new Set(config.keys.filter((k) => k.off_allowed).map((k) => k.key));
	const changes: ConfigChange[] = [];
	const scopes = Object.keys(edits).sort((a, b) =>
		a === BASE ? -1 : b === BASE ? 1 : Number(a) - Number(b)
	);
	for (const scope of scopes) {
		const lane = scope === BASE ? null : Number(scope);
		for (const [key, edit] of Object.entries(edits[scope] ?? {})) {
			if (lane !== null && edit.useBase) {
				if (overridden(config, scope, key)) changes.push({ key, value: 'default', lane });
				continue;
			}
			// The bot takes each value as one line; a rules textarea may hold several.
			const value = edit.value.replace(/\s*[\r\n]+\s*/g, ' ').trim();
			if (value === current(config, scope, key)) continue;
			changes.push({ key, value: value === '' && offAllowed.has(key) ? 'off' : value, lane });
		}
	}
	return changes;
}

export function needsRestart(preview: Preview): boolean {
	return [...preview.base, ...Object.values(preview.lanes).flat()].some(
		(row) => row.apply === 'restart'
	);
}

/** What a change costs once saved: applied by a reload, or a restart of its lane or of every lane. */
export function costLabel(apply: string, scope: Scope): string {
	if (apply === 'live') return 'Applies live';
	return scope === BASE ? 'Restarts the service' : `Restarts lane ${scope}`;
}

/**
 * What Save + restart must restart: the service when a restart-only change is on the base,
 * else each lane with one, so the other lanes keep betting.
 */
export function restartPlan(preview: Preview): { service: boolean; lanes: number[] } {
	const restarts = (rows: DiffRow[]) => rows.some((row) => row.apply === 'restart');
	if (restarts(preview.base)) return { service: true, lanes: [] };
	const lanes = Object.entries(preview.lanes)
		.filter(([, rows]) => restarts(rows))
		.map(([lane]) => Number(lane))
		.sort((a, b) => a - b);
	return { service: false, lanes };
}

export function hasLiveChange(preview: Preview): boolean {
	return [...preview.base, ...Object.values(preview.lanes).flat()].some(
		(row) => row.apply === 'live'
	);
}

/** Each lane's last reload time, to tell a reload that comes after a save from older ones. */
export function reloadMarks(lanes: Lanes): Record<number, number | null> {
	return Object.fromEntries(
		Object.entries(lanes).map(([lane, f]) => [Number(lane), f.last_reload?.ts_ms ?? null])
	);
}

/** Lanes that reloaded since `marks` was taken, with the keys they applied. */
export function appliedSince(
	lanes: Lanes,
	marks: Record<number, number | null>
): Array<{ lane: number; keys: string[] }> {
	return Object.entries(lanes)
		.map(([lane, f]) => ({ lane: Number(lane), reload: f.last_reload }))
		.filter(({ lane, reload }) => reload && reload.ts_ms !== (marks[lane] ?? null))
		.map(({ lane, reload }) => ({ lane, keys: reload?.keys ?? [] }));
}

/** The badge look for a key that applies `live` or after a `restart`. */
export function badgeClass(apply: string): string {
	return apply === 'live'
		? 'rounded border border-profit px-1.5 text-xs text-profit'
		: 'rounded border border-warn px-1.5 text-xs text-warn';
}

export interface Pasted {
	values: Record<string, string>;
	skipped: Array<{ key: string; reason: string }>;
}

const NOT_EDITABLE = 'not editable here';
const LANES_PAGE = 'set it on the Lanes page';
const FIXED_REASONS: Record<string, string> = {
	api_key: "secret: stays in the bot's .env",
	lanes: LANES_PAGE,
	bet_value: 'worked out from multiplier and rule',
	strategy: 'set by strategy_key'
};
const CUT_OFF = 'value is cut off; type it in';
const MASKED = 'shown without its password; type it in';
const NO_PROXY = new Set(['direct', 'none', 'off']);
const TRUE_WORDS = new Set(['on', 'yes', 'true']);
const FALSE_WORDS = new Set(['off', 'no', 'false']);

/**
 * Form values from pasted `key=value` lines or a `wolfctl config-show` table, for the Base
 * tab. A value the table wrapped onto more rows is cut off, so it is skipped rather than
 * half-filled; `api_key` never enters the form.
 */
export function parsePasted(text: string, keys: ConfigKey[]): Pasted {
	const byName = new Map(keys.map((k) => [k.key, k]));
	const raw = new Map<string, string>();
	const wrapped = new Set<string>();
	const skipped: Pasted['skipped'] = [];
	let last: string | null = null;
	for (const line of text.split(/\r?\n/)) {
		const lanes = line.match(/^\s*Lanes:\s*\d+.*?\bsplit:\s*([a-z]+)/i);
		if (lanes) {
			raw.set(LANE_SPLIT, lanes[1] ?? '');
			skipped.push({ key: 'lanes', reason: LANES_PAGE });
			continue;
		}
		const cells = line.includes('│') || line.trim().startsWith('|') ? line.split(/[│|]/) : null;
		if (cells) {
			const [key = '', value = ''] = cells.slice(1).map((c) => c.trim());
			if (!key) {
				if (value && last) wrapped.add(last);
				continue;
			}
			if (!/^[a-z][a-z0-9_]*$/.test(key)) continue;
			raw.set(key, value);
			last = key;
			continue;
		}
		const pair = line.match(/^\s*([a-z][a-z0-9_]*)\s*[=:]\s*(.*)$/);
		if (pair) raw.set(pair[1] ?? '', (pair[2] ?? '').trim());
	}
	const values: Record<string, string> = {};
	for (const [name, rawValue] of raw) {
		const value = rawValue.replace(/\s+\(live\b.*\)$/, '').trim();
		const fixed = FIXED_REASONS[name];
		if (fixed) skipped.push({ key: name, reason: fixed });
		else if (wrapped.has(name) || value.endsWith('...'))
			skipped.push({ key: name, reason: CUT_OFF });
		else if (name === LANE_SPLIT) values[name] = value;
		else {
			const key = byName.get(name);
			if (!key) skipped.push({ key: name, reason: NOT_EDITABLE });
			else if (name === 'proxy' && !NO_PROXY.has(value.toLowerCase()))
				skipped.push({ key: name, reason: MASKED });
			else values[name] = formValue(key, value);
		}
	}
	return { values, skipped };
}

function formValue(key: ConfigKey, value: string): string {
	const word = value.toLowerCase();
	if (fieldKind(key) === 'boolean') {
		if (TRUE_WORDS.has(word)) return 'true';
		if (FALSE_WORDS.has(word)) return 'false';
	}
	if (key.off_allowed && (word === 'off' || (key.key === 'proxy' && NO_PROXY.has(word)))) return '';
	return value;
}
