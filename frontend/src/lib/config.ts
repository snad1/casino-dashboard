import type { components } from '#lib/api/schema';
import type { Lanes } from '#lib/live/protocol';

export type ConfigKey = components['schemas']['ConfigKey'];
export type ConfigOut = components['schemas']['ConfigOut'];
export type ConfigChange = components['schemas']['ConfigChange'];
export type Preview = components['schemas']['PreviewOut'];
export type DiffRow = components['schemas']['DiffRow'];
export type FieldKind = 'select' | 'boolean' | 'number' | 'text';
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
	return 'text';
}

/** A saved value as form text: lists read `N:X`, nothing reads empty. */
export function display(value: unknown): string {
	if (value === null || value === undefined) return '';
	if (Array.isArray(value)) return value.join(':');
	return String(value);
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
			const value = edit.value.trim();
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
