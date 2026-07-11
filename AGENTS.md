# ppt 开发指引

## 项目

`ppt` 是 Python 3.8+ CLI：为中美混合永久投资组合提供计划、再平衡建议和记账。它处理真实持仓与交易记录；README 是行为规约，任何会改变命令、数据格式或金融计算语义的改动都必须同步更新测试和 README。

## 开发约定

- 保持分层：计算层纯函数（无 IO、无 print、无项目内 import）；IO 层处理 OSS、行情与缓存；展示层只组装原子组件；CLI 只做参数、编排和异常处理。
- 所有终端输出经 `ppt.display` 的组件；不要在 CLI 或业务逻辑中散写 `print(Text(...))`。
- 保持交易单位、人民币估值、历史汇率、浮点容差与数据兼容性；涉及买卖、撤销、再平衡时覆盖边界和失败路径。
- 不覆盖或回退已有用户改动；不要把密钥、OSS 凭据或真实持仓写入仓库。

## 验证

```bash
python -m pytest
ruff check .
```

需要本地开发安装时：`pip install -e ".[dev]"`。CLI 入口为 `ppt`；实现细节与命令契约见 `README.md`。

## Flywheel

使用本机已安装的 Flywheel：需求 `/fwp-plan <需求>`，缺陷 `/fwp-debug <问题>`，检查 `/fwp-inspect`，恢复 `/fwp-resume`。`fwp-ship` 负责分支/PR/CI，`fwp-build` 只负责实现和本地验证。遵守本仓库现有规则与授权；GitHub Issue/PR 为交付真值。
