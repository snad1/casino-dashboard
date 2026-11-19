<script lang="ts">
	let { title }: { title: string } = $props();
</script>

<svelte:head><title>{title} · Casino dashboard</title></svelte:head>

<section class="mx-auto max-w-2xl rounded-lg border border-line bg-surface p-6">
	<h1 class="text-xl font-semibold">{title}</h1>
	<p class="mt-2 text-sm text-muted">This page is not built yet.</p>
</section>
