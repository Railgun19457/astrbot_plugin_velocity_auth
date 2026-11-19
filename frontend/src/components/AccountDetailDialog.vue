<template>
  <v-dialog
    v-model="open"
    aria-labelledby="account-detail-title"
    max-width="720"
    scrollable
    :persistent="Boolean(busy)"
  >
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="$account" size="20" />
        <span id="account-detail-title">{{ account?.name || "账号详情" }}</span>
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
        <v-btn
          icon="$close"
          aria-label="关闭账号详情"
          variant="text"
          size="small"
          :disabled="Boolean(busy)"
          @click="open = false"
        />
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
        >
          <template #append>
            <v-btn variant="text" @click="load">重试</v-btn>
          </template>
        </v-alert>
        <template v-else-if="account">
          <v-alert
            v-if="actionError && !confirmation"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-3"
            :text="actionError"
          />
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
                  :disabled="Boolean(busy)"
                  @click="openConfirmation('bindings', binding)"
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
            text="当前服务器仅支持查看账号详情。"
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
                :disabled="Boolean(busy)"
                @click="toggleStatus"
              >
                {{ account.status === "locked" ? "解锁账号" : "锁定账号" }}
              </v-btn>
              <v-btn
                prepend-icon="$password"
                variant="tonal"
                :disabled="Boolean(busy)"
                @click="openConfirmation('password')"
              >
                重置密码
              </v-btn>
              <v-btn
                prepend-icon="$kick"
                variant="tonal"
                color="warning"
                :loading="busy === 'kick'"
                :disabled="Boolean(busy)"
                @click="run('kick', { reason: '管理员已将会话踢出' }, '已踢出在线会话')"
              >
                踢下线
              </v-btn>
              <v-btn
                prepend-icon="$sessions"
                variant="tonal"
                :loading="busy === 'clear-session'"
                :disabled="Boolean(busy)"
                @click="run('clear-session', {}, '已清除会话保持')"
              >
                清除会话保持
              </v-btn>
              <v-btn
                prepend-icon="$error"
                variant="tonal"
                color="error"
                :disabled="Boolean(busy)"
                @click="openConfirmation('delete')"
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
        <v-btn variant="text" :disabled="Boolean(busy)" @click="open = false">关闭</v-btn>
      </v-card-actions>
    </v-card>

    <v-dialog
      :model-value="Boolean(confirmation)"
      aria-labelledby="account-confirmation-title"
      max-width="420"
      :persistent="Boolean(busy)"
      @update:model-value="(visible) => !visible && closeConfirmation()"
    >
      <v-card>
        <v-card-title id="account-confirmation-title">
          {{ confirmationTitles[confirmation] }}
        </v-card-title>
        <v-card-text>
          <v-alert
            v-if="actionError"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-3"
            :text="actionError"
          />
          <v-text-field
            v-if="confirmation === 'password'"
            v-model="newPassword"
            label="新密码"
            type="password"
            autocomplete="new-password"
            density="compact"
            variant="outlined"
            :disabled="Boolean(busy)"
            autofocus
          />
          <template v-else-if="confirmation === 'delete'">
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
              :disabled="Boolean(busy)"
              autofocus
            />
          </template>
          <p v-else-if="bindingToRemove" class="binding-confirmation">
            确认解绑 {{ bindingToRemove.provider }} 的账号 {{ bindingToRemove.identifier }}？
          </p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="Boolean(busy)" @click="closeConfirmation">取消</v-btn>
          <v-btn
            :color="confirmation === 'password' ? 'primary' : 'error'"
            variant="tonal"
            :loading="Boolean(busy)"
            :disabled="Boolean(busy) || !canConfirm"
            @click="submitConfirmation"
          >
            {{ confirmationButtons[confirmation] }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiGet, apiPost } from "../api";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  server: { type: String, default: "" },
  accountKey: { type: String, default: "" },
  canAdmin: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "changed", "toast"]);

const loading = ref(false);
const loadError = ref("");
const actionError = ref("");
const account = ref(null);
const busy = ref("");
let requestId = 0;

const confirmation = ref("");
const bindingToRemove = ref(null);
const newPassword = ref("");
const confirmName = ref("");

const confirmationTitles = { password: "重置密码", delete: "删除账号", bindings: "解绑渠道" };
const confirmationButtons = { password: "确认重置", delete: "确认删除", bindings: "确认解绑" };
const canConfirm = computed(() => {
  if (confirmation.value === "password") return Boolean(newPassword.value);
  if (confirmation.value === "delete") {
    return Boolean(account.value?.name) && confirmName.value === account.value.name;
  }
  return Boolean(bindingToRemove.value);
});

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

async function load() {
  if (!open.value || !props.accountKey) {
    return;
  }
  const currentRequest = ++requestId;
  account.value = null;
  loading.value = true;
  loadError.value = "";
  try {
    const result = await apiGet("page/account", {
      account_key: props.accountKey,
      server: props.server,
    });
    if (currentRequest === requestId) {
      account.value = result.account || null;
      if (!account.value) loadError.value = "未找到该账号";
    }
  } catch (error) {
    if (currentRequest === requestId) {
      loadError.value = error?.message || "加载账号详情失败";
    }
  } finally {
    if (currentRequest === requestId) loading.value = false;
  }
}

async function run(kind, fields, successText) {
  if (busy.value || !props.canAdmin || !account.value) return;
  const { id, name } = account.value;
  const accountKey = Number.isSafeInteger(id) && id > 0 ? `id:${id}` : `name:${name}`;
  actionError.value = "";
  busy.value = kind;
  try {
    await apiPost(`page/account/${kind}`, {
      ...fields,
      account_key: accountKey,
      server: props.server,
    });
    closeConfirmation();
    emit("changed");
    emit("toast", successText);
    if (kind === "delete") {
      open.value = false;
    } else {
      await load();
    }
  } catch (error) {
    actionError.value = error?.message || "操作失败";
  } finally {
    busy.value = "";
  }
}

function toggleStatus() {
  const status = account.value?.status === "locked" ? "active" : "locked";
  run("status", { status }, status === "locked" ? "账号已锁定" : "账号已解锁");
}

function openConfirmation(kind, binding = null) {
  closeConfirmation();
  bindingToRemove.value = binding;
  confirmation.value = kind;
}

function closeConfirmation() {
  confirmation.value = "";
  bindingToRemove.value = null;
  newPassword.value = "";
  confirmName.value = "";
  actionError.value = "";
}

function submitConfirmation() {
  if (!canConfirm.value) return;
  if (confirmation.value === "password") {
    run("password", { password: newPassword.value }, "密码已重置");
  } else if (confirmation.value === "delete") {
    run("delete", { confirm_name: confirmName.value }, "账号已删除");
  } else {
    const { provider } = bindingToRemove.value;
    run("bindings", { provider }, `已解绑 ${provider}`);
  }
}

watch(
  [open, () => props.accountKey, () => props.server],
  () => {
    requestId += 1;
    closeConfirmation();
    if (open.value) {
      load();
    } else {
      account.value = null;
      loadError.value = "";
      loading.value = false;
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
  overflow-wrap: anywhere;
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

.binding-confirmation {
  overflow-wrap: anywhere;
}

.loading-row {
  padding: 40px 0;
  text-align: center;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
</style>
