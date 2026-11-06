<template>
  <div>
    <div class="section-head">
      <div>
        <h2 class="section-title">{{ t("accounts.title", "账号管理") }}</h2>
        <div class="section-caption mt-1">
          共 {{ total }} 个账号 · 第 {{ page }} / {{ totalPages }} 页
        </div>
      </div>
      <div class="d-flex ga-2 align-center flex-wrap">
        <v-btn variant="tonal" prepend-icon="$refresh" :loading="loading" @click="load">
          {{ t("refresh", "刷新") }}
        </v-btn>
      </div>
    </div>

    <v-card variant="outlined" class="rounded-lg mb-4">
      <v-card-text class="pa-4">
        <v-row dense align="center">
          <v-col cols="12" md="4">
            <v-text-field
              v-model="filters.keyword"
              :label="t('accounts.search', '搜索账号名 / UUID / IP / 绑定')"
              prepend-inner-icon="$search"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              @keyup.enter="search"
              @click:clear="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-select
              v-model="filters.status"
              :items="statusOptions"
              label="状态"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              @update:model-value="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-select
              v-model="filters.sort"
              :items="sortOptions"
              label="排序"
              density="compact"
              variant="outlined"
              hide-details
              @update:model-value="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-checkbox
              v-model="filters.online"
              label="仅在线"
              density="compact"
              hide-details
              @update:model-value="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-btn block color="primary" variant="flat" @click="search">
              {{ t("search", "搜索") }}
            </v-btn>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="rounded-lg">
      <v-table hover density="comfortable">
        <thead>
          <tr>
            <th class="text-left">账号</th>
            <th class="text-left">状态</th>
            <th class="text-left">绑定</th>
            <th class="text-left">上次登录</th>
            <th class="text-left">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!accounts.length">
            <td colspan="5" class="empty-cell">
              {{ loading ? "加载中…" : t("accounts.empty", "没有匹配的账号") }}
            </td>
          </tr>
          <tr v-for="account in accounts" :key="account.id">
            <td>
              <div class="d-flex align-center ga-2">
                <v-icon
                  :icon="account.online ? '$sessions' : '$account'"
                  :color="account.online ? 'success' : undefined"
                  size="16"
                />
                <div>
                  <div class="account-name">{{ account.name }}</div>
                  <div class="mono subtle">{{ account.uuid }}</div>
                </div>
              </div>
            </td>
            <td>
              <v-chip size="small" :color="statusColor(account.status)" variant="tonal">
                {{ account.status_label }}
              </v-chip>
            </td>
            <td>
              <div v-if="!account.bindings.length" class="subtle">-</div>
              <div v-else class="d-flex flex-wrap ga-1">
                <v-chip
                  v-for="binding in account.bindings"
                  :key="binding.provider"
                  size="x-small"
                  variant="tonal"
                >
                  {{ binding.provider }}: {{ binding.identifier }}
                </v-chip>
              </div>
            </td>
            <td>
              <div>{{ account.last_login_text }}</div>
              <div class="subtle mono">{{ account.last_ip || "-" }}</div>
            </td>
            <td>
              <v-btn
                size="small"
                variant="text"
                color="primary"
                @click="openDetail(account)"
              >
                详情
              </v-btn>
            </td>
          </tr>
        </tbody>
      </v-table>
      <v-divider />
      <div class="d-flex align-center justify-space-between pa-3">
        <span class="subtle">每页 {{ pageSize }} 条</span>
        <v-pagination
          v-model="page"
          :length="totalPages"
          :total-visible="5"
          density="compact"
          @update:model-value="load"
        />
      </div>
    </v-card>

    <AccountDetailDialog
      v-model="detailOpen"
      :server="server"
      :account-key="detailKey"
      :can-admin="canAdmin"
      @changed="onChanged"
      @error="(message) => emit('error', message)"
      @toast="(message) => emit('toast', message)"
    />
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { apiGet, accountKey } from "../api";
import { t } from "../i18n";
import AccountDetailDialog from "../components/AccountDetailDialog.vue";

const props = defineProps({
  server: { type: String, default: "" },
  refreshKey: { type: Number, default: 0 },
  canAdmin: { type: Boolean, default: false },
});

const emit = defineEmits(["error", "refresh", "toast"]);

const filters = reactive({ keyword: "", status: "", online: false, sort: "" });
const accounts = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const loading = ref(false);

const detailOpen = ref(false);
const detailKey = ref("");

const statusOptions = [
  { title: "正常", value: "active" },
  { title: "已锁定", value: "locked" },
  { title: "待绑定", value: "pending_binding" },
];

const sortOptions = [
  { title: "账号名", value: "" },
  { title: "注册时间 ↓", value: "-created_at" },
  { title: "注册时间 ↑", value: "created_at" },
  { title: "登录时间 ↓", value: "-last_login_at" },
  { title: "登录时间 ↑", value: "last_login_at" },
];

const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize.value)),
);

function statusColor(status) {
  return {
    active: "success",
    locked: "error",
    pending_binding: "warning",
  }[status] || "default";
}

async function load() {
  loading.value = true;
  try {
    const result = await apiGet("page/accounts", {
      server: props.server,
      keyword: filters.keyword || "",
      status: filters.status || "",
      online: filters.online ? "true" : "",
      sort: filters.sort || "",
      page: page.value,
      limit: pageSize.value,
    });
    accounts.value = result.accounts || [];
    total.value = result.total || 0;
  } catch (error) {
    emit("error", error?.message || "加载账号列表失败");
  } finally {
    loading.value = false;
  }
}

function search() {
  page.value = 1;
  load();
}

function openDetail(account) {
  detailKey.value = accountKey(account);
  detailOpen.value = true;
}

function onChanged() {
  load();
  emit("refresh");
}

watch(
  () => [props.server, props.refreshKey],
  () => {
    page.value = 1;
    load();
  },
  { immediate: true },
);
</script>

<style scoped>
.account-name {
  font-weight: 600;
  font-size: 0.9rem;
}

.subtle {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.75rem;
}

.empty-cell {
  text-align: center;
  padding: 32px 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
</style>
