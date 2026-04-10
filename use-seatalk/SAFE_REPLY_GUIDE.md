# Safe Reply Usage Guide

## 问题背景

Main Agent 在发送包含特殊字符（如反引号 `` ` ``）的消息时，会因为 bash 命令替换导致发送失败，然后重新发送修正版本，造成重复消息。

## 解决方案

创建了 `safe-reply.sh` 脚本，提供以下功能：

### 1. 安全转义处理
- 使用 `--stdin` 方法避免 shell 转义问题
- 安全处理反引号、变量、特殊字符

### 2. 消息去重
- 30秒内相似消息自动去重
- 基于消息内容哈希值判断
- 防止意外重复发送

### 3. 错误日志
- 记录所有发送尝试
- 便于问题诊断和调试

## 使用方法

### 基本用法

```bash
# 推荐：使用安全回复
bash .agent/skills/use-seatalk/scripts/safe-reply.sh "消息内容"

# 传统方式（不推荐，可能有转义问题）
bash .agent/skills/use-seatalk/scripts/seatalk-reply.sh "消息内容"
```

### 高级选项

```bash
# 强制发送（跳过去重检查）
bash scripts/safe-reply.sh --force "即使重复也要发送"

# 干运行（测试但不发送）
bash scripts/safe-reply.sh --dry-run "测试消息"

# 纯文本格式
bash scripts/safe-reply.sh --format text "纯文本消息"
```

### 处理特殊字符

```bash
# 安全处理包含特殊字符的消息
bash scripts/safe-reply.sh "包含 \`反引号\` 和 \$变量 的消息"

# 多行消息
bash scripts/safe-reply.sh "第一行
第二行
第三行"
```

## Agent 配置更新

### Main Agent 配置已更新

在 `.agent/agents/main.md` 中：

1. **启动消息**：使用 `safe-reply.sh` 替代 `seatalk-reply.sh`
2. **协调规则**：添加了 SeaTalk 通信指导原则
3. **系统提示**：明确要求使用安全回复方法

### 其他 Agent 建议

所有需要发送 SeaTalk 消息的 Agent 都应该：

```bash
# 在 Agent 配置中使用
bash .agent/skills/use-seatalk/scripts/safe-reply.sh "消息内容"
```

## 日志和监控

### 日志文件位置

- **去重状态**：`.agent/skills/use-seatalk/logs/reply-dedup.state`
- **发送日志**：`.agent/skills/use-seatalk/logs/safe-reply.log`
- **监听器日志**：`.agent/skills/use-seatalk/logs/seatalk-listener.log`

### 查看日志

```bash
# 查看最近的安全回复日志
tail -20 .agent/skills/use-seatalk/logs/safe-reply.log

# 查看去重状态
cat .agent/skills/use-seatalk/logs/reply-dedup.state
```

## 故障排除

### 消息被意外去重

```bash
# 使用 --force 强制发送
bash scripts/safe-reply.sh --force "必须发送的消息"

# 或者等待 30 秒后重试
```

### 特殊字符仍有问题

```bash
# 使用 --dry-run 先测试
bash scripts/safe-reply.sh --dry-run "测试消息"

# 检查日志中的错误信息
tail -10 .agent/skills/use-seatalk/logs/safe-reply.log
```

### 清理去重状态

```bash
# 清空去重状态（谨慎使用）
rm .agent/skills/use-seatalk/logs/reply-dedup.state
```

## 技术实现

### 去重算法

1. 消息标准化：移除多余空格，转换为小写
2. SHA-256 哈希：计算标准化消息的哈希值
3. 时间窗口：30秒内的重复哈希被过滤
4. 自动清理：定期清理过期的去重记录

### 安全转义

1. 使用 `echo "$message" | seatalk-reply.sh --stdin` 方法
2. 避免直接在命令行参数中传递消息
3. 防止 bash 命令替换和变量展开

## 最佳实践

1. **总是使用 `safe-reply.sh`**：替代直接使用 `seatalk-reply.sh`
2. **测试特殊消息**：对包含特殊字符的消息先用 `--dry-run` 测试
3. **监控日志**：定期检查 `safe-reply.log` 中的错误
4. **合理使用 `--force`**：只在确实需要发送重复消息时使用

## 更新历史

- **2026-03-27**: 创建 `safe-reply.sh` 脚本
- **2026-03-27**: 更新 Main Agent 配置使用安全回复
- **2026-03-27**: 添加使用指南和故障排除文档