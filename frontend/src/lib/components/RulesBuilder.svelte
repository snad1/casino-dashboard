<script lang="ts">
	import { plainNumber } from '#lib/format';
	import {
		blankRule,
		describeRule,
		parseRules,
		rulesText,
		takesValue,
		type Rule,
		type RuleOptions
	} from '#lib/rules';
	import Button from '#ui/Button';

	let {
		id,
		value = $bindable(),
		options,
		disabled = false,
		input
	}: {
		/** The JSON textarea's id, so the field label still points at the saved text. */
		id: string;
		value: string;
		options: RuleOptions;
		disabled?: boolean;
		/** The form's input classes. */
		input: string;
	} = $props();

	let tab = $state<'builder' | 'json'>('builder');
	const parsed = $derived(parseRules(value));

	function write(rules: Rule[]) {
		value = rulesText(rules, options);
	}
	function change(i: number, patch: Partial<Rule>) {
		write(parsed.rules.map((rule, j) => (j === i ? { ...rule, ...patch } : rule)));
	}
	function retype(i: number, type: string) {
		write(parsed.rules.map((rule, j) => (j === i ? blankRule(type, options, rule) : rule)));
	}
	function move(i: number, by: number) {
		const rules = [...parsed.rules];
		const [rule] = rules.splice(i, 1);
		if (rule) rules.splice(i + by, 0, rule);
		write(rules);
	}
	function remove(i: number) {
		write(parsed.rules.filter((_, j) => j !== i));
	}
	function add() {
		write([...parsed.rules, blankRule(options.cond_types[0] ?? 'sequence', options)]);
	}
	/** A number typed in a rule field, committed when the field is left; junk keeps the old one. */
	function number(event: Event, i: number, key: 'cond_value' | 'action_value') {
		const target = event.currentTarget as HTMLInputElement;
		const n = Number(target.value.trim());
		if (target.value.trim() !== '' && Number.isFinite(n)) change(i, { [key]: n });
		else target.value = plainNumber(parsed.rules[i]?.[key] ?? 0);
	}

	const select = (extra = '') => `${input} ${extra}`;
	const tabClass = (on: boolean) =>
		`rounded-md border px-3 py-1 text-sm ${on ? 'border-line-strong bg-raised font-medium' : 'border-line text-muted'}`;
</script>

<div class="flex flex-col gap-2">
	<div role="tablist" aria-label="Rules editor" class="flex gap-2">
		<button
			type="button"
			role="tab"
			aria-selected={tab === 'builder'}
			class={tabClass(tab === 'builder')}
			onclick={() => (tab = 'builder')}>Builder</button
		>
		<button
			type="button"
			role="tab"
			aria-selected={tab === 'json'}
			class={tabClass(tab === 'json')}
			onclick={() => (tab = 'json')}>JSON</button
		>
	</div>

	{#if parsed.error}
		<p class="text-sm text-warn" role="status">{parsed.error}</p>
	{/if}

	<textarea
		{id}
		rows="4"
		spellcheck="false"
		class="{input} font-mono"
		hidden={tab !== 'json'}
		{disabled}
		bind:value></textarea>

	{#if tab === 'builder' && !parsed.error}
		<ol class="flex flex-col gap-2" aria-label="Rules">
			{#each parsed.rules as rule, i (i)}
				{@const fields = rule.cond_type === 'profit' ? options.profit_fields : options.bet_fields}
				<li class="flex flex-col gap-2 rounded-md border border-line p-2">
					<p class="font-mono text-sm">{i + 1}. {describeRule(rule, options)}</p>
					<div class="grid gap-2 sm:grid-cols-3">
						<label class="flex flex-col gap-1 text-xs text-muted">
							Condition
							<select
								aria-label="Condition"
								class={select()}
								{disabled}
								value={rule.cond_type}
								onchange={(e) => retype(i, e.currentTarget.value)}
							>
								{#each options.cond_types as type (type)}<option value={type}>{type}</option>{/each}
							</select>
						</label>
						{#if rule.cond_type === 'sequence'}
							<label class="flex flex-col gap-1 text-xs text-muted">
								Mode
								<select
									aria-label="Mode"
									class={select()}
									{disabled}
									value={rule.cond_mode}
									onchange={(e) => change(i, { cond_mode: e.currentTarget.value })}
								>
									{#each options.seq_modes as mode (mode.key)}
										<option value={mode.key}>{mode.label}</option>
									{/each}
								</select>
							</label>
							<label class="flex flex-col gap-1 text-xs text-muted">
								N
								<input
									aria-label="N"
									class={select()}
									inputmode="numeric"
									autocomplete="off"
									{disabled}
									value={plainNumber(rule.cond_value)}
									onchange={(e) => number(e, i, 'cond_value')}
								/>
							</label>
							<label class="flex flex-col gap-1 text-xs text-muted">
								Event
								<select
									aria-label="Event"
									class={select()}
									{disabled}
									value={rule.cond_trigger}
									onchange={(e) => change(i, { cond_trigger: e.currentTarget.value })}
								>
									{#each options.seq_triggers as trigger (trigger)}
										<option value={trigger}>{trigger}</option>
									{/each}
								</select>
							</label>
						{:else}
							<label class="flex flex-col gap-1 text-xs text-muted">
								Field
								<select
									aria-label="Field"
									class={select()}
									{disabled}
									value={rule.cond_field}
									onchange={(e) => change(i, { cond_field: e.currentTarget.value })}
								>
									{#each fields as field (field)}<option value={field}>{field}</option>{/each}
								</select>
							</label>
							<label class="flex flex-col gap-1 text-xs text-muted">
								Operator
								<select
									aria-label="Operator"
									class={select()}
									{disabled}
									value={rule.cond_mode}
									onchange={(e) => change(i, { cond_mode: e.currentTarget.value })}
								>
									{#each options.ops as op (op.key)}<option value={op.key}>{op.label}</option
										>{/each}
								</select>
							</label>
							<label class="flex flex-col gap-1 text-xs text-muted">
								Value
								<input
									aria-label="Value"
									class={select()}
									inputmode="decimal"
									autocomplete="off"
									{disabled}
									value={plainNumber(rule.cond_value)}
									onchange={(e) => number(e, i, 'cond_value')}
								/>
							</label>
						{/if}
						<label class="flex flex-col gap-1 text-xs text-muted">
							Action
							<select
								aria-label="Action"
								class={select()}
								{disabled}
								value={rule.action}
								onchange={(e) => change(i, { action: e.currentTarget.value, action_value: 0 })}
							>
								{#each options.actions as action (action.key)}
									<option value={action.key}>{action.label}</option>
								{/each}
							</select>
						</label>
						{#if takesValue(rule.action, options)}
							<label class="flex flex-col gap-1 text-xs text-muted">
								Action value
								<input
									aria-label="Action value"
									class={select()}
									inputmode="decimal"
									autocomplete="off"
									{disabled}
									value={plainNumber(rule.action_value)}
									onchange={(e) => number(e, i, 'action_value')}
								/>
							</label>
						{/if}
					</div>
					<div class="flex flex-wrap gap-2">
						<Button
							disabled={disabled || i === 0}
							onclick={() => move(i, -1)}
							aria-label="Move rule {i + 1} up">Up</Button
						>
						<Button
							disabled={disabled || i === parsed.rules.length - 1}
							onclick={() => move(i, 1)}
							aria-label="Move rule {i + 1} down">Down</Button
						>
						<Button
							variant="danger"
							{disabled}
							onclick={() => remove(i)}
							aria-label="Remove rule {i + 1}">Remove</Button
						>
					</div>
				</li>
			{:else}
				<li class="text-sm text-muted">No rules yet.</li>
			{/each}
		</ol>
		<div><Button {disabled} onclick={add}>Add rule</Button></div>
	{/if}
</div>
