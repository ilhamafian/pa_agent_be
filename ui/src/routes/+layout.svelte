<script>
  let { children } = $props();
  import "../app.css";
</script>
<section class="max-w-xl mx-auto min-h-screen border-2 border-sky-200 rounded-md p-4">
    {@render children()}
  </section>