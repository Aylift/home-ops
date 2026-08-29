<script setup>
import { cn } from "@/lib/utils";

const props = defineProps({
  class: { type: [Boolean, null, String, Object, Array], required: false },
});
</script>

<template>
  <div
    data-slot="card-title"
    :class="cn('leading-none font-semibold', props.class)"
  >
    <slot />
  </div>
</template>
