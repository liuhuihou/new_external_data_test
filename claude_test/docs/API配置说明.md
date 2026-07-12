# API 配置说明

后端读取同目录下的 `api_config.json`。配置文件用于控制“智能报告”阶段调用哪个 API、用哪个模型、怎么鉴权。

## 1. 前端配置

打开独立的「API 设置」页（`api_settings.html`，可从工作台右上角进入），采用左右双栏的 Profile 管理：

**左栏 — Profiles 列表**

- 每个 Profile 一张卡片，显示名称、模型、API 类型。
- ★ 标记当前生效（active）的 Profile；高亮边框标记正在编辑的 Profile。
- `+ 新增` 创建自定义 Profile；卡片上的 ✏ 重命名、🗑 删除（仅自定义 Profile 可改；内置 `anthropic` / `openai_compatible` / `zhipu` 不可删改）。
- 点击卡片 = 选中编辑，不会改动当前生效项。

**右栏 — 编辑区**

- 顶部「设为当前生效」可把正在编辑的 Profile 切为 active；API Key 输入框右侧 👁 可显示/隐藏。
- 字段：API 类型、模型、请求地址、Base URL、API Key、Auth Token、超时、Temperature、Max tokens。
- `保存配置` 写回 `api_config.json`；`连通性测试` 用当前表单内容即时调用，不写盘（未保存也能测）。

## 2. 默认：zhipu

工作区默认预置 `zhipu` profile，走 OpenAI 兼容接口调用智谱 GLM：

```json
{
  "active": "zhipu"
}
```

`zhipu` profile 已配好请求地址与模型，只需在 `api_config.json` 或网页配置区填入 API Key 即可：

- `api_type`：`openai`
- `api_url`：`https://open.bigmodel.cn/api/paas/v4/chat/completions`
- `model`：`glm-5.2`

## 3. Anthropic / Claude

```json
{
  "active": "anthropic",
  "profiles": {
    "anthropic": {
      "api_type": "anthropic",
      "base_url": "https://api.anthropic.com",
      "api_key": "你的 API Key",
      "auth_token": "",
      "model": "claude-3-5-sonnet-20241022",
      "anthropic_version": "2023-06-01",
      "temperature": 0.2,
      "max_tokens": 4096,
      "timeout_seconds": 60
    }
  }
}
```

说明：

- `base_url` 会自动拼接成 `/v1/messages`
- `api_key` 会作为 `x-api-key` 请求头发送
- `auth_token` 会作为 `Authorization: Bearer ...` 请求头发送

## 4. OpenAI 兼容接口

```json
{
  "active": "openai_compatible",
  "profiles": {
    "openai_compatible": {
      "api_type": "openai",
      "api_url": "https://api.openai.com/v1/chat/completions",
      "api_key": "你的 API Key",
      "model": "gpt-4.1-mini",
      "temperature": 0.2,
      "timeout_seconds": 60
    }
  }
}
```

说明：

- `api_key` 会作为 `Authorization: Bearer ...` 请求头发送
- 也支持第三方 OpenAI 兼容网关

## 5. 配置字段

- `active`：当前启用的 profile
- `profiles`：profile 集合
- `api_type`：`anthropic` / `openai`
- `api_url`：完整请求地址
- `base_url`：基础地址，Anthropic 会自动补 `/v1/messages`
- `api_key`：API Key
- `auth_token`：额外鉴权 token
- `model`：模型名称
- `temperature`：温度
- `max_tokens`：最大输出 token
- `timeout_seconds`：超时秒数
- `custom_headers`：额外请求头

## 6. 安全说明

调用外部 API 时，后端只发送聚合摘要：

- 字段映射
- 样本概况
- 数据质量指标
- 分箱结果
- Cutoff 规则
- IV / KS / AUC / PSI

不会发送客户明细行。
