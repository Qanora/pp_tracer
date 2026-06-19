# PP Tracer

Point-to-Point Tracer — 延迟追踪与网络路径分析工具。

## 四层开发飞轮

本项目使用四层飞轮（Flywheel）开发模型实现从需求到合入的全流程自动化。

| 层 | Skill     | 职责                              |
| -- | --------- | --------------------------------- |
| 0  | `/lp-up`  | 引擎观察：执行+分析运行时数据      |
| 0  | `/lp-dp`  | 飞轮自检：分析飞轮执行上下文       |
| 1  | `/lp-ms`  | Issue 生命周期管理                |
| 2  | `/lp-mr`  | MR 全生命周期管理（git 操作）     |
| 3  | `/lp-dev` | 纯本地开发：实现/修复 → 验证       |

详见 [CLAUDE.md](./CLAUDE.md)。

## 快速开始

```bash
# 安装依赖
uv sync

# 运行测试
uv run pytest -v

# 运行 CLI
uv run pptracer --help
```
