export type Tone = 'success' | 'error' | 'info';

export interface Toast {
	id: number;
	tone: Tone;
	text: string;
}

export const TOAST_MS = 6000;

/** Short notices about finished actions, shown in the app's toast region. */
export const toasts = $state<{ items: Toast[] }>({ items: [] });

let nextId = 1;

/** Show `text` for a few seconds; returns its id so it can be dismissed sooner. */
export function toast(
	tone: Tone,
	text: string,
	after: (run: () => void, ms: number) => unknown = setTimeout
): number {
	const id = nextId++;
	toasts.items.push({ id, tone, text });
	after(() => dismiss(id), TOAST_MS);
	return id;
}

export function dismiss(id: number): void {
	toasts.items = toasts.items.filter((t) => t.id !== id);
}
