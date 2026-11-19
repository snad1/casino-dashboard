import { describe, expect, it } from 'vitest';
import { dismiss, toast, toasts, TOAST_MS } from './toast.svelte';

describe('toast', () => {
	it('shows a notice and removes it after its time or on dismiss', () => {
		const timers: Array<{ run: () => void; ms: number }> = [];
		const after = (run: () => void, ms: number) => timers.push({ run, ms });
		const first = toast('success', 'Saved.', after);
		toast('error', 'Failed.', after);
		expect(toasts.items.map((t) => t.text)).toEqual(['Saved.', 'Failed.']);
		expect(timers.at(0)?.ms).toBe(TOAST_MS);
		timers.at(0)?.run();
		expect(toasts.items.map((t) => t.text)).toEqual(['Failed.']);
		dismiss(toasts.items.at(0)?.id ?? 0);
		expect(toasts.items).toEqual([]);
		expect(first).toBeGreaterThan(0);
	});
});
