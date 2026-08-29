<script setup>
import { cn } from "@/lib/utils";

const props = defineProps({
  class: { type: [Boolean, null, String, Object, Array], required: false },
});
</script>

<template>
  <div data-slot="card-content" :class="cn('px-6', props.class)">
    <slot />
  </div>
</template>
