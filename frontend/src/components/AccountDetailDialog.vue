<template>
  <v-dialog v-model="open" max-width="720" scrollable>
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="$account" size="20" />
        <span>{{ account?.name || "账号详情" }}</span>
        <v-chip
          v-if="account"
          size="small"
          :color="statusColor"
          variant="tonal"
          class="ml-2"
        >
          {{ account.status_label }}
        </v-chip>
        <v-spacer />
        <v-btn icon="$close" variant="text" size="small" @click="open = false" />
      </v-card-title>

      <v-divider />

      <v-card-text>
        <div v-if="loading" class="loading-row">加载中…</div>
        <v-alert
          v-else-if="loadError"
          type="error"
          variant="tonal"
          density="comfortable"
          :text="loadError"
        />
        <template v-else-if="account">
          <v-row dense>
            <v-col cols="12" sm="6">
              <div class="field-label">ID</div>
              <div class="field-value mono">{{ account.id }}</div>
            </v-col>
            <v-col cols="12" sm="6">
              <div class="field-label">UUID</div>
              <div class="field-value mono">{{ account.uuid }}</div>
            </v-col>
            <v-col cols="12" sm="6">
              <div class="field-label">注册时间</div>
              <div class="field-value">{{ account.created_text }}</div>
            </v-col>
            <v-col cols="12" sm="6">
              <div class="field-label">上次登录</div>
              <div class="field-value">
                {{ account.last_login_text }}
                <span class="subtle mono">{{ account.last_ip || "" }}</span>
              </div>
            </v-col>
            <v-col cols="12" sm="6">
              <div class="field-label">上次子服</div>
              <div class="field-value">{{ account.last_server || "-" }}</div>
            </v-col>
          </v-row>

          <div class="section-label">渠道绑定</div>
          <div v-if="!account.bindings.length" class="subtle">未绑定任何渠道</div>
          <v-list v-else density="compact" class="pa-0">
            <v-list-item
              v-for="binding in account.bindings"
              :key="binding.provider"
              class="px-0"
            >
              <v-list-item-title>
                <span class="binding-provider">{{ binding.provider }}</span>
                <span class="mono">{{ binding.identifier }}</span>
              </v-list-item-title>
              <v-list-item-subtitle>
                验证于 {{ binding.verified_text }}
              </v-list-item-subtitle>
              <template #append>
                <v-btn
                  v-if="canAdmin"
                  size="small"
                  variant="text"
                  color="error"
                  @click="confirmUnbind(binding)"
                >
                  解绑
                </v-btn>
              </template>
            </v-list-item>
          </v-list>
          <v-alert
            v-if="!canAdmin"
            type="info"
            variant="tonal"
            density="compact"
            class="mt-3"
            text="服务端未启用管理接口（bot.admin.enabled），当前为只读模式。"
          />

          <div class="section-label">身份</div>
          <div v-if="!account.identities.length" class="subtle">无身份记录</div>
          <div v-else class="d-flex flex-wrap ga-2">
            <v-chip
              v-for="identity in account.identities"
              :key="`${identity.kind}:${identity.identifier}`"
              size="small"
              variant="tonal"
            >
              {{ identity.kind }}: {{ identity.identifier }}
            </v-chip>
          </div>

          <template v-if="canAdmin">
            <v-divider class="my-4" />
            <div class="section-label">管理操作</div>
            <div class="d-flex flex-wrap ga-2">
              <v-btn
                :prepend-icon="account.status === 'locked' ? '$unlock' : '$lock'"
                variant="tonal"
                :color="account.status === 'locked' ? 'success' : 'warning'"
                :loading="busy === 'status'"
                @click="toggleStatus"
              >
                {{ account.status === "locked" ? "解锁账号" : "锁定账号" }}
              </v-btn>
              <v-btn
                prepend-icon="$password"
                variant="tonal"
                :loading="busy === 'password'"
                @click="passwordDialog = true"
              >
                重置密码
              </v-btn>
              <v-btn
                prepend-icon="$kick"
                variant="tonal"
                color="warning"
                :loading="busy === 'kick'"
                @click="kick"
              >
                踢下线
              </v-btn>
              <v-btn
                prepend-icon="$sessions"
                variant="tonal"
                :loading="busy === 'clear'"
                @click="clearSession"
              >
                清除会话保持
              </v-btn>
              <v-btn
                prepend-icon="$error"
                variant="tonal"
                color="error"
                :loading="busy === 'delete'"
                @click="deleteDialog = true"
              >
                删除账号
              </v-btn>
            </div>
          </template>
        </template>
      </v-card-text>

      <v-divider />
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="open = false">关闭</v-btn>
      </v-card-actions>
    </v-card>

    <!-- 重置密码 -->
    <v-dialog v-model="passwordDialog" max-width="420" persistent>
      <v-card>
        <v-card-title>重置密码</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="newPassword"
            label="新密码"
            type="password"
            density="compact"
            variant="outlined"
            autofocus
          />
          <div class="subtle mt-2">该操作不会校验旧密码，并会写入审计日志。</div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="passwordDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            :loading="busy === 'password'"
            :disabled="!newPassword"
            @click="resetPassword"
          >
            确认重置
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 删除账号 -->
    <v-dialog v-model="deleteDialog" max-width="420" persistent>
      <v-card>
        <v-card-title>删除账号</v-card-title>
        <v-card-text>
          <v-alert
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-3"
            text="删除后无法恢复，绑定历史会保留。请输入账号名确认。"
          />
          <v-text-field
            v-model="confirmName"
            :label="`输入 ${account?.name || ''} 以确认`"
            density="compact"
            variant="outlined"
            autofocus
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteDialog = false">取消</v-btn>
          <v-btn
            color="error"
            :loading="busy === 'delete'"
            :disabled="confirmName !== (account?.name || '')"
            @click="deleteAccount"
          >
            确认删除
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiDelete, apiGet, apiPost } from "../api";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  server: { type: String, default: "" },
  accountKey: { type: String, default: "" },
  canAdmin: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "changed", "error", "toast"]);

const loading = ref(false);
const loadError = ref("");
const account = ref(null);
const busy = ref("");

const passwordDialog = ref(false);
const newPassword = ref("");
const deleteDialog = ref(false);
const confirmName = ref("");

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

const statusColor = computed(() => {
  return (
    {
      active: "success",
      locked: "error",
      pending_binding: "warning",
    }[account.value?.status] || "default"
  );
});

const keyPath = computed(() => encodeURIComponent(props.accountKey || ""));

async function load() {
  if (!props.accountKey) {
    return;
  }
  loading.value = true;
  loadError.value = "";
  try {
    const result = await apiGet(`page/accounts/${keyPath.value}`, {
      server: props.server,
    });
    account.value = result.account || null;
  } catch (error) {
    loadError.value = error?.message || "加载账号详情失败";
  } finally {
    loading.value = false;
  }
}

async function run(kind, action, successText = "操作成功") {
  busy.value = kind;
  try {
    await action();
    await load();
    emit("changed");
    emit("toast", successText);
    return true;
  } catch (error) {
    emit("error", error?.message || "操作失败");
    return false;
  } finally {
    busy.value = "";
  }
}

function toggleStatus() {
  const next = account.value?.status === "locked" ? "active" : "locked";
  run(
    "status",
    () =>
      apiPost(`page/accounts/${keyPath.value}/status`, {
        status: next,
        server: props.server,
      }),
    next === "locked" ? "账号已锁定" : "账号已解锁",
  );
}

function resetPassword() {
  run(
    "password",
    async () => {
      await apiPost(`page/accounts/${keyPath.value}/password`, {
        password: newPassword.value,
        server: props.server,
      });
      passwordDialog.value = false;
      newPassword.value = "";
    },
    "密码已重置",
  );
}

function kick() {
  run(
    "kick",
    () =>
      apiPost(`page/accounts/${keyPath.value}/kick`, {
        reason: "管理员已将会话踢出",
        server: props.server,
      }),
    "已踢出在线会话",
  );
}

function clearSession() {
  run(
    "clear",
    () =>
      apiPost(`page/accounts/${keyPath.value}/clear-session`, {
        server: props.server,
      }),
    "已清除会话保持",
  );
}

function deleteAccount() {
  run(
    "delete",
    async () => {
      await apiPost(`page/accounts/${keyPath.value}/delete`, {
        confirm_name: confirmName.value,
        server: props.server,
      });
      deleteDialog.value = false;
      confirmName.value = "";
      open.value = false;
    },
    "账号已删除",
  );
}

function confirmUnbind(binding) {
  const provider = binding.provider;
  if (!window.confirm(`确认解绑 ${provider}: ${binding.identifier}？`)) {
    return;
  }
  run(
    `unbind:${provider}`,
    () =>
      apiPost(
        `page/accounts/${keyPath.value}/bindings/${encodeURIComponent(provider)}`,
        { server: props.server },
      ),
    `已解绑 ${provider}`,
  );
}

watch(
  () => [props.modelValue, props.accountKey],
  () => {
    if (props.modelValue) {
      load();
    }
  },
  { immediate: true },
);
</script>

<style scoped>
.field-label {
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-surface), 0.55);
  margin-bottom: 2px;
}

.field-value {
  font-size: 0.88rem;
  margin-bottom: 12px;
}

.section-label {
  font-size: 0.82rem;
  font-weight: 600;
  margin: 16px 0 8px;
}

.subtle {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
}

.binding-provider {
  font-weight: 600;
  margin-right: 8px;
}

.loading-row {
  padding: 40px 0;
  text-align: center;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
</style>
