<script setup lang="ts">
import { ref, watch } from 'vue'
import { request, entityName, type Entity, space, refresh } from '../api/client'
const props = defineProps<{ modelValue?: string | null, kind: string }>()
const emit = defineEmits(['update:modelValue'])
const options = ref<Entity[]>([]), loading = ref(false)
async function load(search = '') {
  loading.value = true
  try {
    const response = await request<{items:Entity[]}>(`/${props.kind}?page_size=50&search=${encodeURIComponent(search)}`)
    options.value = response.items
    if (props.modelValue && !options.value.some(x => x.id === props.modelValue)) {
      const selected = await request<Entity>(`/${props.kind}/${props.modelValue}`)
      options.value.unshift(selected)
    }
  } finally { loading.value = false }
}
watch(() => [props.kind, props.modelValue, space.value, refresh.value], () => load(), {immediate:true})
</script>
<template><el-select :model-value="modelValue" @update:model-value="emit('update:modelValue', $event || null)" filterable remote :remote-method="load" clearable :loading="loading" placeholder="搜索并选择记录" style="width:100%"><el-option v-for="row in options" :key="row.id" :label="entityName(row)" :value="row.id" /></el-select></template>
