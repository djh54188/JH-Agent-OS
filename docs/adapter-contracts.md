# 模型适配器统一契约与配置规范

- **版本**：v1.1.0
- **适用角色**：Worker (Gemini / 备选)、Reviewer (Muse / 备选)、Meta Planner (人工 / 专有模型)
- **设计目标**：将上层 Orchestrator 业务编排与底层具体供应商 API 解耦，保证协议一致性、安全凭据隔离与可插拔扩展。

---

## 1. 统一适配器接口契约（Python 抽象定义）

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

@dataclass
class AdapterResponse:
    content: str
    parsed_json: Optional[Dict[str, Any]]
    model_id: str
    tokens_prompt: Optional[int]
    tokens_completion: Optional[int]
    total_tokens: Optional[int]
    latency_ms: int
    is_mock: bool
    raw_response: Optional[Dict[str, Any]]

class ModelAdapter(ABC):
    """JH-Agent OS 统一模型适配器基类"""

    @abstractmethod
    def get_role(self) -> str:
        """返回适配器分配的角色：worker | reviewer | meta_planner"""
        pass

    @abstractmethod
    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        """标准文本交互"""
        pass

    @abstractmethod
    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        """
        结构化约束输出：强制模型按照指定的 JSON Schema 产生结果，
        并对模型输出进行本地 Schema 验证，验证不通过则抛出 SchemaValidationError。
        Codex 适配器通过 --output-schema 与 --json 实现原生受约束解析。
        """
        pass

    @abstractmethod
    def check_health(self) -> Dict[str, Any]:
        """连通性与凭据探测"""
        pass
```

---

## 2. 供应商接入核查与能力矩阵清单

根据路线图 v1.1 要求，核实 Gemini 与 Muse 的供应商、模型 ID、端点、认证方式及必要能力：

| 维度 | Gemini (首选 Worker) | Muse (首选 Reviewer) | 备选/本地可用项 (DeepSeek) | 内核引擎 (OpenAI Codex) |
| :--- | :--- | :--- | :--- | :--- |
| **供应商** | Google (Google AI / Vertex AI) | 用户提供的本地 Muse 代理（上游供应商未核实） | 深度求索 (DeepSeek) | OpenAI |
| **基准模型 ID** | `gemini-1.5-pro`（可选 `2.0-flash`） | `muse-spark-1.3-contributor-free` | `deepseek-flash` / `v4-pro` | `gpt-6.1-sol` |
| **API 端点** | `https://generativelanguage.googleapis.com/v1beta` | `http://127.0.0.1:8787/v1`（Responses） | `https://api.deepseek.com/v1` | 本地 CLI / `stdio://` |
| **认证方式** | HTTP Header `x-goog-api-key` 或 Bearer Token | `Authorization: Bearer <KEY>` | HTTP Header `Authorization: Bearer <KEY>` | OAuth Bearer / API Key |
| **凭据环境变量** | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | `MUSE_API_KEY` | `DEEPSEEK_API_KEY` | `~/.codex/auth.json` |
| **核心必要能力** | 结构化输出 (JSON Schema)、大上下文、工具调用 | 严格结构化输出 (`ReviewResult`)、Diff 审查 | 结构化输出 (JSON Mode)、代码理解 | 代码编辑沙箱、执行环境反射 |
| **本地环境现状** | **未配置凭据**（阻塞真实调用） | **已配置；两项最小真实审核通过**（见 `reports/muse-verification.json`） | **已具备有效 API 凭据并连通验证** | **已安装 CLI 0.160.1 并实测连通** |

---

## 3. Worker 与 Reviewer 差异化配置与原则铁律

### 3.1 核心原则铁律
1. **Worker 完成不等于任务成功**：Worker 生成的代码、计划或测试通过，仅代表执行产出；必须经过 Reviewer 独立质量审核出具 `pass` 或满足显式 `acceptance_criteria` 方可进入 `succeeded`。
2. **Reviewer 不可用绝不得自动视为通过**：当 Reviewer 遭遇超时、API 故障或格式错误时，控制层严禁静默放行，必须转为 `needs_attention` 或受控置为 `failed`。
3. **审核结论四级处置分野**：
   - `pass` -> 任务成功（`succeeded`）
   - `revise` -> 缺陷可修复，触发有限修复重审闭环（`repairing`，最多 2 轮）
   - `reject` -> 严重缺陷或方向性违背，不可修复终态（`failed`）
   - `needs_human` -> 规则争议或人工审批分歧，转交人工介入（`needs_attention`）

### 3.2 Worker (首选 Gemini)
- **职责**：接收任务，编写计划、生成代码补丁、产出 `ToolRequest`。
- **推荐基准模型**：`gemini-1.5-pro`（深度推理）；探索/速度场景可用 `gemini-2.0-flash`。
- **参数倾向**：
  - `temperature`: 0.2（代码与工程任务需稳定输出）
  - `max_tokens`: 8192
  - `structured_output`: 启用（输出 Plan Schema 与 ToolRequest Schema）
- **安全拦截策略**：Worker 生成的所有工具调用请求必须经由控制层逐调用动态鉴权，Worker 本身无直接执行权。

### 3.3 Reviewer (首选 Muse)
- **职责**：独立于 Worker 的无偏见质量与安全审核。针对 Worker 产出的代码补丁或方案，生成严格符合 `review_result.v1.json` 的判定结果。
- **参数倾向**：
  - `reasoning.effort`: `max`（用户指定；不声明完全确定性）
  - `max_output_tokens`: 131072（用户提供的上限；最小验证使用 8192）
  - `structured_output`: 强制启用（必须产出包含 `evidence` 的 Issue 列表）
- **纪律约束**：不参与任务编写；不直接修改代码；发现缺陷必须给出具体行号与复现证据。

---

## 4. 凭据与安全规范

1. **绝对禁令**：禁止在代码库、Git 提交、日志记录及 Markdown 报告中硬编码任何 API 密钥或 Token。
2. **安全读取**：适配器配置仅声明环境变量名称（如 `api_key_env: "GEMINI_API_KEY"`），运行时由进程从系统安全环境变量中读取。
3. **日志脱敏**：任何涉及请求头或响应调试输出的模块，必须对 `Authorization`、`x-goog-api-key` 等敏感字段实施自动掩码（Masking: `mkt_***` 或 `AIza***`）。

## 5. Muse Responses 实际配置（2026-10-10）

实际配置读取 `configs/adapters.local.json`，不存在时使用示例配置。
调用入口为 `src.model_config.load_model_adapter("reviewer")`；Responses 适配器只调用 `/responses`，不自动改用 Chat Completions。
上下文 1,000,000 与输出上限 131,072 来自用户配置，尚未进行满容量压测。
详细操作与限制见 `docs/muse-setup.md`；最小真实验证结果见 `reports/muse-verification.json`。
