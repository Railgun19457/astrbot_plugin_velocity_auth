import { ref } from "vue";
import { getBridge } from "./bridge";

export const locale = ref("zh-CN");
export const isDark = ref(false);
export const revision = ref(0);

export function syncContext(context) {
  if (!context || typeof context !== "object") {
    return;
  }
  if (typeof context.locale === "string" && context.locale) {
    locale.value = context.locale;
  }
  if (typeof context.isDark === "boolean") {
    isDark.value = context.isDark;
  }
  revision.value += 1;
}

/**
 * 翻译页面文案。
 *
 * @param {string} key 相对 `pages.dashboard` 的点路径
 * @param {string} fallback 插件 i18n 无对应条目时的回退文案
 * @param {Record<string, unknown>} vars `{name}` 占位符的取值
 * @returns {string} 解析后的文案
 */
export function t(key, fallback = "", vars = {}) {
  revision.value;
  let text = fallback;
  try {
    const bridge = getBridge();
    if (typeof bridge.t === "function") {
      text = bridge.t(`pages.dashboard.${key}`, fallback) || fallback;
    }
  } catch {
    text = fallback;
  }
  return String(text).replace(/\{(\w+)\}/g, (match, name) =>
    name in vars ? String(vars[name]) : match,
  );
}
