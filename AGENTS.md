# AGENTS.md — Science Buddy 仓库协作约定

本文件供 AI 编码代理（Copilot、Cursor、Claude Code、Codex 等）与人类贡献者共同遵守。设计参照 [RAGFlow 的 AGENTS.md](https://github.com/infiniflow/ragflow) 仓库约定实践。

## 先读

1. 动手前必读 `docs/project-memory.md`（仓库级工程记忆与变更账本；源码、迁移与测试是最终权威）。
2. 目录布局与维护公约见 `docs/structure.md`；一次性脚本/历史产物一律放 `temp/`（见 `temp/README.md`），不要在根目录或 `apps/api/data/` 遗留新文件。
3. 系统架构见 `docs/architecture.md`（英文）与 `docs/architecture-zh.md`（中文，历史快照）；项目全貌见 `docs/project-introduction.md`。

## 硬性约束

- 本地优先的生命科学科研助手：不得用于诊断/临床决策；不伪造模型执行、证据、文献标识、产品货号、引用或向量构建结果。
- 可追溯性不变量：Evidence ID 签名、source locator、collection/project 隔离、检索 trace、头脑风暴原件不可变。
- 模型辅助输出必须使用显式 Pydantic 契约；所有引用的 Evidence ID 必须通过机械校验；未知 ID 递归清除。
- 未经显式授权，不得把用户 PDF/方案内容发送给云端模型；PDF 解析、分块、抽取摘要、标签与 E5 嵌入是本地流程，禁止悄悄改走云端。
- 数据库变更必须写 Alembic 迁移（fresh upgrade → downgrade → upgrade 验证），升级前备份 `apps/api/data/science_buddy.db` 到 `apps/api/data/backups/`。
- 依赖变更须同步 `pyproject.toml` 与 `package.json`；不引入 LangChain/LangGraph 等重框架作为运行时依赖。

## 验证门槛（完成前必须全绿）

从 `apps/api`（使用项目解释器，如 `D:\ProgramData\anaconda3\python.exe` 或 `.venv\Scripts\python.exe`）：

```text
python -m ruff check src tests alembic
python -m mypy src
python -m pytest
```

从仓库根目录：

```text
npm run lint:web
npm run typecheck:web
npm run build:web
```

先跑聚焦测试，再跑全量；未解决错误不得报告完成。检索质量回归用 `python -m science_buddy.evaluation data/eval/queries.jsonl`（在 `apps/api` 下）。

## 变更账本

每轮产生工程变更后，在 `docs/project-memory.md` 末尾追加带日期的变更日志条目：目标、改动文件/模块、决策、迁移/数据操作、验证命令与结果、失败与遗留。禁止在账本、日志、测试或回复中写入 API Key、令牌、密码、患者标识或私有论文全文。
