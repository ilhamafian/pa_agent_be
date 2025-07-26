<script>
  import { onMount } from "svelte";

    let data = { username: "", status: "" };
  
    onMount(async () => {
      const res = await fetch("http://localhost:8000/api/status");
      data = await res.json();
    });
  </script>
  
<div class="p-4">
    <h1 class="text-xl font-bold text-sky-600">{data.status}</h1>
  <p class="text-gray-700">Welcome, {data.username} 👋</p>
    Redirect to whatsapp
</div>