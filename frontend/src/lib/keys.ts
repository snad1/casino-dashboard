import type { components } from '#lib/api/schema';
import type { ConfigKey } from '#lib/config';

export type VaultKey = components['schemas']['KeyOut'];
export type KeyKind = VaultKey['kind'];

const KEY_ID = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const SECRET = /^[\x21-\x7e]{8,4096}$/;
// eslint-disable-next-line no-control-regex -- a label must not carry control characters
const CONTROL = /[\x00-\x1f\x7f]/;
const PROXY_SCHEMES = ['http://', 'https://', 'socks5://'];

/** `Friend A` -> `friend-a`: the vault id the api stores a label under, or null if none fits. */
export function keyIdFor(label: string): string | null {
	const slug = label
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 32)
		.replace(/-+$/, '');
	return KEY_ID.test(slug) ? slug : null;
}

/** Why `label` can't name a key, or null when it can. */
export function labelError(label: string): string | null {
	if (label.length === 0 || label.length > 64) return 'Use 1-64 characters.';
	if (CONTROL.test(label) || label !== label.trim())
		return 'Use printable characters without spaces at either end.';
	if (keyIdFor(label) === null) return 'Use at least one letter or digit.';
	return null;
}

/** Why `value` can't be stored as a `kind` secret, or null when it can. Never repeats it. */
export function secretError(kind: KeyKind, value: string): string | null {
	if (!SECRET.test(value)) return 'Use 8-4096 printable characters with no spaces.';
	if (kind === 'proxy' && !PROXY_SCHEMES.some((scheme) => value.startsWith(scheme)))
		return 'A proxy starts with http://, https:// or socks5://.';
	return null;
}

/** `lane 1, 2`, or `not used`. */
export function usedBy(lanes: number[]): string {
	return lanes.length ? `lane ${lanes.join(', ')}` : 'not used';
}

/** The config fields with `key_ref` and `proxy_ref` offering the vault's ids of their kind. */
export function withVaultChoices(keys: ConfigKey[], vault: VaultKey[]): ConfigKey[] {
	const ids = (kind: KeyKind) => vault.filter((k) => k.kind === kind).map((k) => k.id);
	return keys.map((key) => {
		if (key.key === 'key_ref') return { ...key, choices: ids('key') };
		if (key.key === 'proxy_ref') return { ...key, choices: ids('proxy') };
		return key;
	});
}
