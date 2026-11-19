import { getBridge } from "./bridge";

async function request(method, endpoint, payload) {
  const bridge = getBridge();
  // Vue 的响应式代理无法被 structured-clone，统一降级为纯 JSON。
  const plainPayload = JSON.parse(JSON.stringify(payload));
  const result = await bridge[method](endpoint, plainPayload);
  if (result?.status === "error") {
    throw new Error(result.message || "请求失败");
  }
  return result;
}

export const apiGet = (endpoint, params = {}) => request("apiGet", endpoint, params);
export const apiPost = (endpoint, body = {}) => request("apiPost", endpoint, body);
