# Changelog

## v1.0.3 (2026-07-22)

### 新增

- **简化消息格式**：新增 `simple_mode` 配置项，开启后仅发送单行摘要，避免大型服务器刷屏。
- **全局配置恢复**：恢复 `_conf_schema.json`，修复上一版本删除该文件导致全局配置异常无法显示的问题。

### 优化

- **代码审查重构**：抽取 `BaseMcClient` 基类，消除两个 Client 类中重复的 session 管理代码。
- **统一配置解析**：提取 `_resolve_effective_cfg()` 静态方法，消除 `_set_client` 和 `check_one_group` 中 3 处重复的 API 源配置合并逻辑。
- **简化日志回调**：移除 `_log_buffer_only` 中的自引用 `getattr` 回退逻辑。

### 修复

- **aiohttp Session 泄漏**：配置变更/群组删除时不再直接丢弃旧 client，改为先 `await client.close()` 再替换。
- **监控循环时间漂移**：新增 `_sleep_with_stop_check()` 动态补偿本轮检测耗时，确保实际循环间隔稳定。
- **消息发送失败**：还原 `resolve_platform_id()` 遍历平台管理器查找真实实例 ID 的逻辑（修复固定返回 `"aiocqhttp"` 导致 `cannot find platform for session` 的问题）。
- **mcstatus 版本显示**：`version.name` → `version.name_clean`，适配新版 mcstatus API。

### 其他

- `_conf_schema.json` 类型修正：`"str"` 改为 `"string"`（AstrBot 标准类型名），新增 `simple_mode` 等缺失配置项。
- 清理 `metadata.yaml` 中冗余的 `config` 字段（AstrBot 配置 schema 统一由 `_conf_schema.json` 管理）。

## v1.0.1 (2026-07-22)

### 新增

- **多 API 源支持**：新增 `mcmotdapi` 作为可选服务器状态查询源，默认使用 `mcstatus.io`。
- **每群独立 API 源配置**：允许为每个群组单独指定 API 查询源（mcstatus.io / mcmotdapi / 全局默认），有效应对不同地区服务器网络可达性问题。
- **全局 API 源配置**：在插件全局设置中新增 `api_source`、`mcmotdapi_host`、`mcmotdapi_ssl` 三个配置项。

### 优化

- **WebUI 模态框交互优化**：点击遮罩层不再自动关闭模态框，防止编辑内容意外丢失。
- **日志增加 API 源信息**：监控检查日志和 `/查询` 命令结果中显示使用的 API 源，便于定位问题。


### 兼容性

- 向后兼容 v1.0.0 所有配置，**无需数据迁移**。
- 默认行为不变：不修改配置的情况下，查询行为与旧版完全一致。
