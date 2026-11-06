<template>
  <div>
    <div class="section-head">
      <div>
        <h2 class="section-title">{{ t("overview.statsTitle", "服务器概览") }}</h2>
        <div class="section-caption mt-1">{{ rangeCaption }}</div>
      </div>
      <v-select
        v-model="days"
        :items="dayOptions"
        density="compact"
        variant="outlined"
        hide-details
        style="max-width: 168px"
        :loading="loading"
        @update:model-value="load"
      />
    </div>

    <v-row dense>
      <v-col cols="12" sm="6" md="3">
        <StatCard
          :label="t('overview.accountsTotal', '账号总数')"
          :value="data?.accounts_total ?? '--'"
          :meta="accountMeta"
          icon="$accounts"
        />
      </v-col>
      <v-col cols="12" sm="6" md="3">
        <StatCard
          :label="t('overview.online', '当前在线')"
          :value="data?.online ?? '--'"
          :meta="botMeta"
          icon="$sessions"
        />
      </v-col>
      <v-col cols="12" sm="6" md="3">
        <StatCard
          :label="t('overview.dau', '日活（24h）')"
          :value="activity.dau ?? '--'"
          :meta="`周活 ${activity.wau ?? '-'} · 月活 ${activity.mau ?? '-'}`"
          icon="$dashboard"
        />
      </v-col>
      <v-col cols="12" sm="6" md="3">
        <StatCard
          :label="t('overview.logins24h', '近 24h 登录')"
          :value="loginCount('24h')"
          :meta="`注册 ${registeredCount('24h')}`"
          icon="$success"
        />
      </v-col>
    </v-row>

    <v-card variant="outlined" class="rounded-lg mt-4">
      <div class="card-title-row">
        <span class="card-title-text">{{ t("overview.dailyTrend", "每日活跃趋势") }}</span>
        <span class="section-caption">{{ trendCaption }}</span>
      </div>
      <v-divider />
      <v-card-text class="pa-4">
        <TrendLineChart
          :points="trendPoints"
          :empty-text="t('overview.noData', '暂无统计数据')"
        />
      </v-card-text>
    </v-card>

    <v-row dense class="mt-1">
      <v-col cols="12" md="6">
        <v-card variant="outlined" class="rounded-lg h-100">
          <div class="card-title-row">
            <span class="card-title-text">{{ t("overview.statusChart", "账号状态") }}</span>
          </div>
          <v-divider />
          <v-card-text class="pa-4">
            <DonutChart
              :items="statusItems"
              :total-label="t('overview.accountsTotal', '账号总数')"
              :empty-text="t('overview.noData', '暂无统计数据')"
            />
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="6">
        <v-card variant="outlined" class="rounded-lg h-100">
          <div class="card-title-row">
            <span class="card-title-text">{{ t("overview.bindingChart", "渠道绑定") }}</span>
          </div>
          <v-divider />
          <v-card-text class="pa-4">
            <BarList
              :items="bindingItems"
              :empty-text="t('overview.noData', '暂无统计数据')"
            />
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="6">
        <v-card variant="outlined" class="rounded-lg h-100">
          <div class="card-title-row">
            <span class="card-title-text">{{ t("overview.loginChart", "成功登录") }}</span>
          </div>
          <v-divider />
          <v-card-text class="pa-4">
            <BarList :items="loginItems" :empty-text="t('overview.noData', '暂无统计数据')" />
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="6">
        <v-card variant="outlined" class="rounded-lg h-100">
          <div class="card-title-row">
            <span class="card-title-text">{{ t("overview.registerChart", "注册数") }}</span>
          </div>
          <v-divider />
          <v-card-text class="pa-4">
            <BarList
              :items="registeredItems"
              :empty-text="t('overview.noData', '暂无统计数据')"
            />
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiGet } from "../api";
import { t } from "../i18n";
import StatCard from "../components/StatCard.vue";
import TrendLineChart from "../components/TrendLineChart.vue";
import DonutChart from "../components/DonutChart.vue";
import BarList from "../components/BarList.vue";

const props = defineProps({
  server: { type: String, default: "" },
  refreshKey: { type: Number, default: 0 },
});

const emit = defineEmits(["error"]);

const days = ref(30);
const data = ref(null);
const loading = ref(false);

const dayOptions = [
  { title: "近 7 天", value: 7 },
  { title: "近 30 天", value: 30 },
  { title: "近 90 天", value: 90 },
];

const activity = computed(() => data.value?.activity || {});
const statusItems = computed(() => data.value?.status_distribution || []);
const bindingItems = computed(() => data.value?.bindings || []);
const loginItems = computed(() => data.value?.logins || []);
const registeredItems = computed(() => data.value?.registered || []);

const trendPoints = computed(() =>
  (activity.value.trend || []).map((point) => ({
    date: point.date,
    value: point.accounts,
    logins: point.logins,
  })),
);

const rangeCaption = computed(
  () => `数据窗口：${dayOptions.find((item) => item.value === days.value)?.title || "-"}`,
);

const trendCaption = computed(() => {
  const points = trendPoints.value;
  if (!points.length) {
    return "无数据";
  }
  return `${points[0].date} ~ ${points[points.length - 1].date}`;
});

const accountMeta = computed(() => {
  const items = statusItems.value;
  if (!items.length) {
    return "";
  }
  return items.map((item) => `${item.name} ${item.count}`).join(" · ");
});

const botMeta = computed(() => `Bot 连接 ${data.value?.bot_connected ?? "-"}`);

function loginCount(window) {
  const hit = loginItems.value.find((item) => item.name === window);
  return hit ? hit.count : 0;
}

function registeredCount(window) {
  const hit = registeredItems.value.find((item) => item.name === window);
  return hit ? hit.count : 0;
}

async function load() {
  loading.value = true;
  try {
    data.value = await apiGet("page/stats", {
      server: props.server,
      days: days.value,
    });
  } catch (error) {
    emit("error", error?.message || "加载概览失败");
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.server, props.refreshKey],
  () => load(),
  { immediate: true },
);
</script>
