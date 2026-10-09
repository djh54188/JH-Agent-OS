# 模型适配器统一契约与配置规范

- **版本**：v1.0.0
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
    tokens_prompt: int
    tokens_completion: int
    total_tokens: int
    latency_ms: int
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
        并对模型输出进行本地 Schema 验证，验证不通过则抛出 ContractViolationError。
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
| **供应商** | Google (Google AI / Vertex AI) | 待定（未在路线图与环境提供） | 深度求索 (DeepSeek) | OpenAI |
| **推荐模型 ID** | `gemini-1.5-pro` / `gemini-2.0-flash` | 待定 (如 `muse-code-review-v1`) | `deepseek-v4-pro` / `deepseek-flash` | `gpt-6.1-sol` |
| **API 端点** | `https://generativelanguage.googleapis.com/v1beta` | 待架构师提供 | `https://api.deepseek.com/v1` | 本地 CLI / `stdio://` |
| **认证方式** | HTTP Header `x-goog-api-key` 或 Bearer Token | 待定（常规 Bearer Token） | HTTP Header `Authorization: Bearer <KEY>` | OAuth Bearer / API Key |
| **凭据环境变量** | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | `MUSE_API_KEY` | `DEEPSEEK_API_KEY` | `~/.codex/auth.json` |
| **核心必要能力** | 结构化输出 (JSON Schema)、大上下文、工具调用 | 严格结构化输出 (`ReviewResult`)、Diff 审查 | 结构化输出 (JSON Mode)、代码理解 | 代码编辑沙箱、执行环境反射 |
| **本地环境现状** | **未配置凭据**（阻塞真实调用） | **完全缺少接口信息与凭据**（阻塞真实调用） | **已具备有效 API 凭据并连通验证** | **已安装 CLI 0.160.1 并实测连通** |

---

## 3. Worker 与 Reviewer 差异化配置规范

### 3.1 Worker (首选 Gemini)
- **职责**：接收任务，编写计划、生成代码补丁、产出 `ToolRequest`。
- **参数倾向**：
  - `temperature`: 0.2（代码与工程任务需稳定输出）
  - `max_tokens`: 8192
  - `structured_output`: 启用（输出 Plan Schema 与 ToolRequest Schema）
- **安全拦截策略**：Worker 生成的所有工具调用请求必须经由控制层拦截与风险审查，Worker 本身无直接执行权。

### 3.2 Reviewer (首选 Muse)
- **职责**：独立于 Worker 的无偏见质量与安全审核。针对 Worker 产出的代码补丁或方案，生成严格符合 `review_result.v1.json` 的判定结果。
- **参数倾向**：
  - `temperature`: 0.0（完全确定性评判）
  - `max_tokens`: 4096
  - `structured_output`: 强制启用（必须产出包含 `evidence` 的 issue 列表）
- **纪律约束**：不参与任务编写；不直接修改代码；发现缺陷必须给出具体行号与复现证据。

### 3.3 Meta Planner 人工介入通道与契约
- **职责**：架构定稿、跨项目写入裁决、高危操作批准、失败复盘。
- **交互方式**：
  - 命令行交互确认提示（CLI Prompt: Y/N/Modify）；
  - 结构化工单导出（可输出为 `Task` 异常审批单供人工签署）。

---

## 4. 凭据与安全规范

1. **绝对禁令**：禁止在代码库、Git 提交、日志记录及 Markdown 报告中硬编码任何 API 密钥或 Token。
2. **安全读取**：适配器配置仅声明环境变量名称（如 `api_key_env: "GEMINI_API_KEY"`），运行时由进程从系统安全环境变量中读取。
3. **日志脱敏**：任何涉及请求头或响应调试输出的模块，必须对 `Authorization`、`x-goog-api-key` 等敏感字段实施自动掩码（Masking: `mkt_***` 或 `AIza***`）。
