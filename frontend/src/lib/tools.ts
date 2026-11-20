export const MAX_LOG_LINES = 5000;
export const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;
export type Level = (typeof LEVELS)[number];

/** One log line as the bot writes it: `time  LEVEL  [LK] message`, lane 1 untagged. */
export interface LogLine {
	id: number;
	text: string;
	level: Level | null;
	lane: number | null;
}

export interface LogFilter {
	level: Level | 'all';
	lane: number | 'all';
	search: string;
}

const ESCAPES =
	// eslint-disable-next-line no-control-regex -- matching terminal escape codes is the point
	/\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|[\x00-\x08\x0b-\x1f\x7f]/g;
const LINE = /\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(?:\[L(\d{1,2})\]\s)?/;

export function stripAnsi(text: string): string {
	return text.replace(ESCAPES, '');
}

/** Read the level and lane of a log line; a line the bot did not format keeps both null. */
export function parseLogLine(id: number, raw: string): LogLine {
	const text = stripAnsi(raw);
	const match = LINE.exec(text);
	if (!match) return { id, text, level: null, lane: null };
	return {
		id,
		text,
		level: match[1] as Level,
		lane: match[2] ? Number(match[2]) : 1
	};
}

export function filterLines(lines: LogLine[], filter: LogFilter): LogLine[] {
	const search = filter.search.trim().toLowerCase();
	return lines.filter(
		(line) =>
			(filter.level === 'all' || line.level === filter.level) &&
			(filter.lane === 'all' || line.lane === filter.lane) &&
			(!search || line.text.toLowerCase().includes(search))
	);
}

/** `lines` plus `more`, keeping only the newest `MAX_LOG_LINES`. */
export function capLines<T>(lines: T[], more: T[], max = MAX_LOG_LINES): T[] {
	const next = lines.concat(more);
	return next.length > max ? next.slice(next.length - max) : next;
}

/** A backtest progress line from stderr, `{"progress": 0..1}`, as 0..1; null for any other line. */
export function parseProgress(line: string): number | null {
	try {
		const value: unknown = JSON.parse(line);
		if (typeof value !== 'object' || value === null) return null;
		const progress = (value as Record<string, unknown>).progress;
		return typeof progress === 'number' ? Math.min(1, Math.max(0, progress)) : null;
	} catch {
		return null;
	}
}

export type BacktestResult = Record<string, unknown> & { v: 1; mode: string };

/** The backtest's result document: the last stdout line that parses as one. */
export function parseBacktest(lines: string[]): BacktestResult | null {
	for (let i = lines.length - 1; i >= 0; i--) {
		try {
			const value: unknown = JSON.parse(lines[i] ?? '');
			if (
				typeof value === 'object' &&
				value !== null &&
				(value as Record<string, unknown>).v === 1
			) {
				return value as BacktestResult;
			}
		} catch {
			continue;
		}
	}
	return null;
}

/** The figures to show for a result: its summary when it has one, else its top-level numbers. */
export function resultRows(result: BacktestResult): Array<[string, string]> {
	const source =
		typeof result.summary === 'object' && result.summary !== null
			? (result.summary as Record<string, unknown>)
			: result;
	return Object.entries(source)
		.filter(([key, value]) => key !== 'v' && (typeof value !== 'object' || value === null))
		.map(([key, value]) => [key, value === null ? 'none' : String(value)]);
}

/** Where the browser downloads an export job's file; the api names it, never the host. */
export function downloadUrl(service: string, jobId: string, format: 'csv' | 'json'): string {
	return `/api/${encodeURIComponent(service)}/jobs/${encodeURIComponent(jobId)}/file?format=${format}`;
}
