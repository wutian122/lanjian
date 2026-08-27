# agent-task-cleanup

## Purpose

定义 agent 审计任务进入终态后清理 `/tmp/lanjian/<task_id>` 临时源码工作目录的行为，防止 tmpfs 累积塞满导致任务失败与容器误报 unhealthy。

## Requirements

### Requirement: 任务收尾清理临时源码目录

The system SHALL 在 agent 审计任务进入终态（成功、失败、取消）或异常退出后，删除该任务在 `/tmp/lanjian/<task_id>` 下的临时源码工作目录，释放 tmpfs 空间。

#### Scenario: 任务正常完成后清理

- **WHEN** 一个 agent 审计任务正常完成（status=COMPLETED）
- **THEN** 系统删除 `/tmp/lanjian/<task_id>` 目录

#### Scenario: 任务失败后清理

- **WHEN** 一个 agent 审计任务失败（status=FAILED）
- **THEN** 系统删除 `/tmp/lanjian/<task_id>` 目录

#### Scenario: 任务取消后清理

- **WHEN** 一个 agent 审计任务被取消（CancelledError 或用户主动取消）
- **THEN** 系统删除 `/tmp/lanjian/<task_id>` 目录

#### Scenario: 清理异常不阻断收尾

- **WHEN** 清理目录时发生异常（如目录已被外部删除或权限不足）
- **THEN** 系统忽略清理异常（ignore_errors），不阻断任务状态落库与内存清理

### Requirement: 重新验证 finding 兼容已清理目录

The system SHALL 在 `reverify_finding` 时，若任务的临时源码目录已被清理，对 ZIP 类型项目重新解压源码以恢复沙箱挂载。

#### Scenario: ZIP 项目目录被清理后重验

- **WHEN** 用户对 ZIP 类型项目的 finding 调用重验，且 `/tmp/lanjian/<task_id>` 目录不存在
- **THEN** 系统复用 `_get_project_root` 重新解压项目 ZIP 到该目录，再执行 PoC 验证

#### Scenario: 仓库项目目录被清理后重验

- **WHEN** 用户对仓库类型项目的 finding 调用重验，且 `/tmp/lanjian/<task_id>` 目录不存在
- **THEN** 系统返回明确的 409 错误，提示"源码已清理，请重新执行审计任务"

### Requirement: 删除任务同步清理临时目录

The system SHALL 在删除 agent 任务（`delete_agent_task`）时，同步清理该任务的临时源码工作目录。

#### Scenario: 删除任务清理目录

- **WHEN** 用户删除一个 agent 任务
- **THEN** 系统清理 `/tmp/lanjian/<task_id>` 目录，并在清理结果 `cleanedFiles` 中反映该清理
