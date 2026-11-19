<script lang="ts">
	import ActionButton from './ActionButton.svelte';
</script>

<div class="flex items-center gap-2" role="group" aria-label="Kill switch">
	<ActionButton name="pause" body={{ target: 'all' }} label="Pause all" compact />
	<div class="rounded-md border border-dashed border-loss">
		<ActionButton name="stop_all" danger compact />
	</div>
</div>
