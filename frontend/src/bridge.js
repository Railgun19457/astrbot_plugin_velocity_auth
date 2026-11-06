/**
 * AstrBot 插件页面的桥接层。
 *
 * 页面运行在沙箱 iframe 中（没有 allow-same-origin），所有请求必须走注入的
 * `AstrBotPluginView` SDK。下面的 fallback 只用于直接打开页面时开发调试。
 */

let cachedBridge = null;

function createDevFallbackBridge() {
  const apiBase = "/api/plugin/astrbot_plugin_velocity_auth/";

  const parse = async (response) => {
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body?.message) {
          message = body.message;
        }
      } catch {
        // 忽略非 JSON 响应
      }
      throw new Error(message);
    }
    return response.json();
  };

  return {
    ready: async () => ({}),
    getContext: () => ({}),
    onContext: () => () => {},
    t: (key, fallback) => fallback ?? key,
    apiGet: async (endpoint, params = {}) => {
      const url = new URL(`${apiBase}${endpoint}`, window.location.origin);
      Object.entries(params || {}).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          url.searchParams.set(key, value);
        }
      });
      return parse(await fetch(url, { credentials: "same-origin" }));
    },
    apiPost: async (endpoint, body = {}) =>
      parse(
        await fetch(`${apiBase}${endpoint}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(body),
        }),
      ),
    apiDelete: async (endpoint, body = {}) =>
      parse(
        await fetch(`${apiBase}${endpoint}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(body),
        }),
      ),
    subscribeSSE: async () => "",
    unsubscribeSSE: async () => undefined,
  };
}

export function getBridge() {
  if (window.AstrBotPluginView) {
    if (cachedBridge !== window.AstrBotPluginView) {
      cachedBridge = window.AstrBotPluginView;
    }
    return cachedBridge;
  }
  if (!cachedBridge) {
    cachedBridge = createDevFallbackBridge();
  }
  return cachedBridge;
}

export async function waitForBridge() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (window.AstrBotPluginView) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const bridge = getBridge();
  try {
    await bridge.ready();
  } catch {
    // ready() 失败只说明 dashboard 没及时应答，不影响后续调用
  }
  return bridge;
}

/** 若 SDK 在 fallback 之后才注入，重新取一次。 */
export function refreshBridge() {
  if (window.AstrBotPluginView) {
    cachedBridge = window.AstrBotPluginView;
  }
  return getBridge();
}
