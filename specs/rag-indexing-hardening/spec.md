# rag-indexing-hardening

## Purpose

本 delta 为 RAG 索引在含前端构建产物/巨型压缩文件项目上的超时与嵌入静默故障补充防御性行为，
使索引进度可推进、可区分、可失败告警，杜绝零向量静默入库。

## Requirements

### Requirement: 构建产物目录与压缩文件排除

The system SHALL 在 RAG 全量与增量索引的文件收集阶段，按路径段排除前端构建产物目录
（含 `static/assets`、`static/js`、`console-ui`、`public` 等），并按"单行超长或文件超大"识别
minified 产物并跳过索引。

#### Scenario: 含 static/assets 的巨型项目

- **WHEN** 索引目录包含 `static/next/assets/` 下的巨型压缩 JS
- **THEN** 该文件被跳过且不进入分块循环，索引进度持续推进

#### Scenario: 单行超长压缩文件

- **WHEN** 文本文件出现超过阈值的单行（minified 特征）
- **THEN** 该文件判定为构建产物跳过，不进行 tree-sitter 解析

#### Scenario: 合法的大文件源码

- **WHEN** 文件较大但为正常多行源码
- **THEN** 不被误伤，仍正常进入分块

### Requirement: 单文件分块防护

The system SHALL 对单个文件的 tree-sitter 分块设置时间上限（20 秒）与每文件 chunk 数量上限（500）；
超限时跳过该文件并记录 warning，不中断整体索引。

#### Scenario: 巨型文件分块超时

- **WHEN** 某文件分块超过 20 秒
- **THEN** 该文件被跳过，索引继续处理后续文件，进度消息注明跳过原因

#### Scenario: chunk 数量爆炸

- **WHEN** 单文件产生的 chunk 数超过 500
- **THEN** 超出部分被截断并记录 warning，该文件仍计入已处理

### Requirement: 分块循环有界并发

The system SHALL 以有界并发（并发数 4）处理文件分块，使普通文件不被单个慢文件阻塞，
且进度计数单调递增。

#### Scenario: 快慢文件混合

- **WHEN** 一批文件中混有 1 个慢文件与多个快文件
- **THEN** 快文件在慢文件处理期间仍持续推进，整体吞吐不因单文件降为零

#### Scenario: 进度一致性

- **WHEN** 并发分块完成一批文件
- **THEN** 进度计数与串行处理结果一致且单调递增

### Requirement: 嵌入失败快速失败

The system SHALL 在嵌入批次首批失败时立即将索引标记为 RAG 不可用、跳过剩余嵌入批次，
并记录明确告警；系统 MUST NOT 为失败的批次写入零向量。

#### Scenario: 嵌入端点拒绝请求（400）

- **WHEN** 嵌入请求被端点拒绝（如模型不存在）
- **THEN** 索引标记 RAG 不可用、记录明确告警、跳过剩余嵌入，不生成零向量，审计以基础模式继续

#### Scenario: 瞬时抖动后的重试成功

- **WHEN** 嵌入批次首次失败但后续重试成功
- **THEN** 索引正常继续，不误判为不可用

### Requirement: 进度消息分阶段

The system SHALL 将分块阶段与嵌入阶段的进度消息分别标识，使用户可区分当前所处阶段。

#### Scenario: 大项目进度展示

- **WHEN** 索引进度消息处于分块阶段（如 26%）
- **THEN** 该消息明确标识当前处于分块阶段，嵌入阶段使用独立的消息标识
