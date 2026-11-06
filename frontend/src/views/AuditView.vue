<template>
  <div>
    <div class="section-head">
      <div>
        <h2 class="section-title">{{ t("audit.title", "审计日志") }}</h2>
        <div class="section-caption mt-1">
          共 {{ total }} 条 · 第 {{ page }} / {{ totalPages }} 页
        </div>
      </div>
      <v-btn variant="tonal" prepend-icon="$refresh" :loading="loading" @click="load">
        {{ t("refresh", "刷新") }}
      </v-btn>
    </div>

    <v-card variant="outlined" class="rounded-lg mb-4">
      <v-card-text class="pa-4">
        <v-row dense align="center">
          <v-col cols="12" md="3">
            <v-select
              v-model="filters.action"
              :items="actionOptions"
              label="动作"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              @update:model-value="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-select
              v-model="filters.source"
              :items="sourceOptions"
              label="来源"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              @update:model-value="search"
            />
          </v-col>
          <v-col cols="6" md="3">
            <v-text-field
              v-model="filters.actor"
              label="操作者"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              @keyup.enter="search"
              @click:clear="search"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-text-field
              v-model="filters.since"
              label="起始时间（unix 秒）"
              density="compact"
              variant="outlined"
              hide-details
              clearable
              @keyup.enter="search"
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
            <th class="text-left" style="width: 170px">时间</th>
            <th class="text-left">动作</th>
            <th class="text-left">账号</th>
            <th class="text-left">操作者</th>
            <th class="text-left">IP</th>
            <th class="text-left">详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!entries.length">
            <td colspan="6" class="empty-cell">
              {{ loading ? "加载中…" : "没有匹配的审计记录" }}
            </td>
          </tr>
          <tr v-for="entry in entries" :key="entry.id">
            <td class="mono">{{ entry.at_text }}</td>
            <td>
              <v-chip size="small" :color="actionColor(entry.action)" variant="tonal">
                {{ actionLabel(entry.action) }}
              </v-chip>
            </td>
            <td>
              <span v-if="entry.account">{{ entry.account }}</span>
              <span v-else-if="entry.account_id" class="mono subtle">
                #{{ entry.account_id }}
              </span>
              <span v-else class="subtle">-</span>
            </td>
            <td>
              <div>{{ entry.actor || "-" }}</div>
              <div v-if="entry.source" class="subtle">{{ sourceLabel(entry.source) }}</div>
            </td>
            <td class="mono">{{ entry.ip || "-" }}</td>
            <td class="detail-cell">{{ entry.detail || "-" }}</td>
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
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { apiGet } from "../api";
import { t } from "../i18n";

const props = defineProps({
  server: { type: String, default: "" },
  refreshKey: { type: Number, default: 0 },
});

const emit = defineEmits(["error"]);

const filters = reactive({ action: "", source: "", actor: "", since: "" });
const entries = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const loading = ref(false);

const actionOptions = [
  { title: "注册", value: "register" },
  { title: "登录", value: "login" },
  { title: "登录失败", value: "login_failed" },
  { title: "登出", value: "logout" },
  { title: "绑定", value: "bind" },
  { title: "解绑", value: "unbind" },
  { title: "修改密码", value: "change_password" },
  { title: "找回密码", value: "recover" },
  { title: "管理员锁定", value: "admin_lock" },
  { title: "管理员解锁", value: "admin_unlock" },
  { title: "管理员重置密码", value: "admin_reset_password" },
  { title: "管理员删除", value: "admin_delete" },
  { title: "管理员解绑", value: "admin_unbind" },
  { title: "管理员踢下线", value: "admin_kick" },
  { title: "管理员清除会话", value: "admin_clear_session" },
];

const sourceOptions = [
  { title: "游戏内命令", value: "command" },
  { title: "Bot 管理", value: "bot" },
  { title: "插件内部", value: "plugin" },
];

const ACTION_LABELS = Object.fromEntries(
  actionOptions.map((item) => [item.value, item.title]),
);

const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize.value)),
);

function actionLabel(action) {
  return ACTION_LABELS[action] || action || "-";
}

function sourceLabel(source) {
  return (
    { command: "游戏内命令", bot: "Bot 管理", plugin: "插件内部" }[source] || source
  );
}

function actionColor(action) {
  if (action?.startsWith("admin_")) {
    return "warning";
  }
  if (action === "login_failed") {
    return "error";
  }
  if (action === "login" || action === "register") {
    return "success";
  }
  return "default";
}

async function load() {
  loading.value = true;
  try {
    const result = await apiGet("page/audit", {
      server: props.server,
      action: filters.action || "",
      source: filters.source || "",
      actor: filters.actor || "",
      since: filters.since || 0,
      page: page.value,
      limit: pageSize.value,
    });
    entries.value = result.entries || [];
    total.value = result.total || 0;
  } catch (error) {
    emit("error", error?.message || "加载审计日志失败");
  } finally {
    loading.value = false;
  }
}

function search() {
  page.value = 1;
  load();
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
.subtle {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.75rem;
}

.empty-cell {
  text-align: center;
  padding: 32px 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.detail-cell {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
</style>
