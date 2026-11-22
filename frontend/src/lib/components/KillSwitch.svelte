<script lang="ts">
	import ActionButton from './ActionButton.svelte';
</script>

<div class="flex flex-wrap items-end gap-2" role="group" aria-label="All lanes">
	<ActionButton name="pause" body={{ target: 'all' }} label="Pause all" compact />
	<ActionButton name="resume" body={{ target: 'all' }} label="Resume all" compact />
	<div class="rounded-md border border-dashed border-loss">
		<ActionButton name="stop_all" danger compact />
	</div>
</div>
