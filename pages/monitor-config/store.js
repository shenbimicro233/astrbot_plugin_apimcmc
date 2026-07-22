/**
 * 轻量响应式状态管理。
 *
 * 核心思路：
 *   状态集中管理 + 按 key 订阅变化 → 驱动 UI 按需更新。
 *   无需第三方库，无 CDN 依赖。
 *
 * 用法：
 *   const store = new Store({ key: value });
 *   store.get('key');              // 读
 *   store.set('key', newVal);      // 写，自动通知监听器
 *   store.batch({ k1: v1, k2:v2 }); // 批量写，一次通知
 *   const unsub = store.on('key', (newVal, oldVal) => { ... });
 *   unsub();  // 取消订阅
 */
export class Store {
  #state;
  #listeners;

  constructor(initial = {}) {
    this.#state = { ...initial };
    this.#listeners = new Map();
  }

  get(key) {
    return this.#state[key];
  }

  set(key, value) {
    const old = this.#state[key];
    if (old === value) return;
    this.#state[key] = value;
    this.#emit(key, value, old);
  }

  /** 批量写入，每 key 只通知一次。 */
  batch(updates) {
    const changed = [];
    for (const [key, value] of Object.entries(updates)) {
      if (this.#state[key] !== value) {
        this.#state[key] = value;
        changed.push(key);
      }
    }
    for (const key of changed) {
      this.#emit(key, this.#state[key], undefined);
    }
  }

  /** 订阅某 key 的变化，返回取消订阅函数。 */
  on(key, fn) {
    if (!this.#listeners.has(key)) this.#listeners.set(key, []);
    this.#listeners.get(key).push(fn);
    return () => {
      const fns = this.#listeners.get(key);
      if (fns) {
        const idx = fns.indexOf(fn);
        if (idx >= 0) fns.splice(idx, 1);
      }
    };
  }

  #emit(key, value, old) {
    const fns = this.#listeners.get(key);
    if (fns) {
      // 复制一份，防止订阅者在回调中取消自身导致遍历异常
      [...fns].forEach(fn => fn(value, old));
    }
  }
}
