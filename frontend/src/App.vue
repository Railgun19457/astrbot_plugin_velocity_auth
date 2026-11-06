<template>
  <v-app :theme="themeName">
    <v-main class="page-main">
      <div class="page-shell">
        <header class="page-header">
          <div class="page-header__text">
            <h1 class="page-title">{{ t("title", "VelocityAuth 管理台") }}</h1>
            <div class="page-subtitle">
              <span v-if="pluginState?.plugin_version">
                v{{ pluginState.plugin_version }}
              </span>
              <span v-if="currentServer" class="d-inline-flex align-center ga-1">
                <v-icon
                  :icon="currentServer.ready ? '$success' : '$error'"
                  size="12"
                  :color="currentServer.ready ? 'success' : 'error'"
                />
                {{ currentServer.name }} @ {{ currentServer.address }}
              </span>
            </div>
          </div>

          <div class="page-header__actions">
            <v-select
              v-if="serverOptions.length > 1"
              v-model="selectedServer"
              :items="serverOptions"
              label="服务器"
              density="compact"
              variant="outlined"
              hide-details
              class="page-server-select"
            />
            <v-chip
              v-if="currentServer && !currentServer.supports_admin"
              size="small"
              variant="tonal"
              color="warning"
            >
              只读
            </v-chip>
            <v-btn
              variant="tonal"
              prepend-icon="$refresh"
              :loading="loadingState"
              @click="reloadAll"
            >
              {{ t("refresh", "刷新") }}
            </v-btn>
          </div>
        </header>

        <v-tabs v-model="tab" color="primary" density="comfortable" class="page-tabs">
          <v-tab value="overview" prepend-icon="$dashboard">
            {{ t("nav.overview", "仪表盘") }}
          </v-tab>
          <v-tab value="accounts" prepend-icon="$accounts">
            {{ t("nav.accounts", "账号管理") }}
          </v-tab>
          <v-tab value="audit" prepend-icon="$audit">
            {{ t("nav.audit", "审计日志") }}
          </v-tab>
        </v-tabs>

        <v-alert
          v-if="loadError"
          type="error"
          variant="tonal"
          density="comfortable"
          closable
          class="mt-4"
          :text="loadError"
          @click:close="loadError = ''"
        />

        <v-alert
          v-if="pluginState && !pluginState.initialized"
          type="warning"
          variant="tonal"
          density="comfortable"
          class="mt-4"
          text="尚未配置任何服务器模板，请先在插件配置里添加 servers 条目。"
        />

        <v-window v-model="tab" class="page-window">
          <v-window-item value="overview">
            <OverviewView :server="selectedServer" :refresh-key="refreshKey" @error="notify" />
          </v-window-item>
          <v-window-item value="accounts">
            <AccountsView
              :server="selectedServer"
              :refresh-key="refreshKey"
              :can-admin="Boolean(currentServer?.supports_admin)"
              @error="notify"
              @toast="(message) => notify(message, 'success')"
              @refresh="refreshKey += 1"
            />
          </v-window-item>
          <v-window-item value="audit">
            <AuditView :server="selectedServer" :refresh-key="refreshKey" @error="notify" />
          </v-window-item>
        </v-window>
      </div>
    </v-main>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="2600">
      {{ snackbar.text }}
    </v-snackbar>
  </v-app>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { waitForBridge } from "./bridge";
import { apiGet } from "./api";
import { isDark, syncContext, t } from "./i18n";
import { THEME_DARK, THEME_LIGHT } from "./theme";
import OverviewView from "./views/OverviewView.vue";
import AccountsView from "./views/AccountsView.vue";
import AuditView from "./views/AuditView.vue";

const tab = ref("overview");
const pluginState = ref(null);
const loadingState = ref(false);
const loadError = ref("");
const refreshKey = ref(0);
const selectedServer = ref("");

const snackbar = reactive({ show: false, text: "", color: "success" });

const themeName = computed(() => (isDark.value ? THEME_DARK : THEME_LIGHT));

const serverOptions = computed(() =>
  (pluginState.value?.servers || []).map((server) => ({
    title: server.ready ? `${server.name}（已连接）` : server.name,
    value: server.name,
  })),
);

const currentServer = computed(() => {
  const servers = pluginState.value?.servers || [];
  return servers.find((server) => server.name === selectedServer.value) || servers[0] || null;
});

function notify(message, color = "error") {
  if (!message) {
    return;
  }
  snackbar.text = message;
  snackbar.color = color;
  snackbar.show = true;
}

async function loadState() {
  loadingState.value = true;
  loadError.value = "";
  try {
    pluginState.value = await apiGet("page/state");
    if (!selectedServer.value) {
      selectedServer.value = pluginState.value?.default_server || "";
    }
  } catch (error) {
    loadError.value = error?.message || "加载插件状态失败";
  } finally {
    loadingState.value = false;
  }
}

async function reloadAll() {
  await loadState();
  refreshKey.value += 1;
}

watch(
  () => selectedServer.value,
  () => {
    refreshKey.value += 1;
  },
);

onMounted(async () => {
  const bridge = await waitForBridge();
  syncContext(bridge.getContext?.());
  if (typeof bridge.onContext === "function") {
    bridge.onContext(syncContext);
  }
  await loadState();
});
</script>

<style>
html,
body {
  margin: 0;
  padding: 0;
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  font-family:
    "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, -apple-system,
    "Segoe UI", Roboto, sans-serif;
}

.v-application {
  background: rgb(var(--v-theme-background));
}

.page-main {
  background: rgb(var(--v-theme-background));
}

.page-shell {
  width: 100%;
  padding: 16px 16px 32px;
}

/* Vuetify 3.7 的 outlined 卡片用 currentColor 描边，在 AstrBot 的柔和边框旁太重 */
.v-card--variant-outlined {
  border-color: rgba(var(--v-theme-on-surface), 0.12) !important;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.page-title {
  font-size: 1.65rem;
  font-weight: 700;
  letter-spacing: 0;
  line-height: 1.25;
  margin: 0;
}

.page-subtitle {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.page-server-select {
  flex: 1 1 auto;
  min-width: 180px;
  max-width: 280px;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.page-tabs {
  border-bottom: 1px solid rgba(var(--v-border-color), 0.24);
}

.page-window {
  padding-top: 16px;
}

.section-title {
  font-size: 1.15rem;
  font-weight: 700;
  margin: 0;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.section-caption {
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
}

.card-title-text {
  font-size: 0.98rem;
  font-weight: 600;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
}

.subtle {
  color: rgba(var(--v-theme-on-surface), 0.55);
}
</style>
