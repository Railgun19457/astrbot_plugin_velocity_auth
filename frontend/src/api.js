import { getBridge, refreshBridge } from "./bridge";

async function request(method, endpoint, payload) {
  const bridge = getBridge();
  const send =
    method === "get"
      ? bridge.apiGet
      : method === "delete"
        ? bridge.apiDelete || bridge.apiPost
        : bridge.apiPost;
  // Vue 的响应式代理无法被 structured-clone，统一降级为纯 JSON。
  const plainPayload =
    payload === undefined ? {} : JSON.parse(JSON.stringify(payload));
  try {
    const result = await send.call(bridge, endpoint, plainPayload);
    if (result?.status === "error") {
      throw new Error(result.message || "请求失败");
    }
    return result;
  } catch (error) {
    const upgraded = refreshBridge();
    if (upgraded === bridge) {
      throw error;
    }
    const fallbackSend =
      method === "get"
        ? upgraded.apiGet
        : method === "delete"
          ? upgraded.apiDelete || upgraded.apiPost
          : upgraded.apiPost;
    const result = await fallbackSend.call(upgraded, endpoint, plainPayload);
    if (result?.status === "error") {
      throw new Error(result.message || "请求失败");
    }
    return result;
  }
}

export const apiGet = (endpoint, params = {}) => request("get", endpoint, params);
export const apiPost = (endpoint, body = {}) => request("post", endpoint, body);
// AstrBot 的页面桥只暴露 apiGet / apiPost，删除类操作统一走 POST。
export const apiDelete = (endpoint, body = {}) => request("post", endpoint, body);

export const accountKey = (account) => {
  if (account?.id) {
    return `id:${account.id}`;
  }
  return `name:${encodeURIComponent(account?.name || "")}`;
};
