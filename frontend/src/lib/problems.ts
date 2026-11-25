import { laneLabel } from '#lib/lanes';
import type { Lanes, SystemMessage } from '#lib/live/protocol';

export interface Problem {
	text: string;
	/** The lane a Start button would bring back, when there is one. */
	lane?: number;
}

export const AGENT_SILENT = 'The bot host is not answering; actions and live numbers may be late.';

/** What needs the owner now: a lane that should bet but doesn't while others do, and a bot
 * host that stopped answering. Nothing is listed while the whole bot is off on purpose. */
export function problems(lanes: Lanes, agent: SystemMessage['agent'] | undefined): Problem[] {
	const out: Problem[] = [];
	if (agent === 'unavailable') out.push({ text: AGENT_SILENT });
	const betting = Object.values(lanes).some((l) => l.status === 'running' || l.status === 'paused');
	if (!betting) return out;
	for (const [key, lane] of Object.entries(lanes).sort(([a], [b]) => Number(a) - Number(b))) {
		if (lane.status === 'down')
			out.push({ text: `${laneLabel(Number(key), lanes)} is not betting.`, lane: Number(key) });
	}
	return out;
}
