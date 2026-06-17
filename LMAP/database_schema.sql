-- 大模型聚合平台 MySQL 8.0 建表脚本
-- 覆盖：用户鉴权、模型供应商、API Key、Prompt、对话历史、计费额度、审计日志

create database if not exists llm_aggregation
  default character set utf8mb4
  collate utf8mb4_0900_ai_ci;

use llm_aggregation;

create table users (
  id bigint primary key auto_increment,
  username varchar(64) not null unique comment '登录名',
  display_name varchar(80) not null comment '显示名称',
  email varchar(160) null unique,
  password_hash varchar(255) not null comment '密码哈希',
  status tinyint not null default 1 comment '1启用 0禁用',
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp
) comment '用户表';

create table roles (
  id bigint primary key auto_increment,
  code varchar(40) not null unique comment 'admin/user/auditor',
  name varchar(80) not null,
  created_at datetime not null default current_timestamp
) comment '角色表';

create table user_roles (
  user_id bigint not null,
  role_id bigint not null,
  primary key (user_id, role_id),
  constraint fk_user_roles_user foreign key (user_id) references users(id),
  constraint fk_user_roles_role foreign key (role_id) references roles(id)
) comment '用户角色关系表';

create table model_providers (
  id bigint primary key auto_increment,
  provider_code varchar(40) not null unique comment 'openai/anthropic/deepseek/qwen/zhipu',
  provider_name varchar(80) not null,
  base_url varchar(255) not null,
  protocol varchar(40) not null default 'openai-compatible' comment '接口协议',
  enabled tinyint not null default 1,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp
) comment '模型供应商表';

create table models (
  id bigint primary key auto_increment,
  provider_id bigint not null,
  model_code varchar(100) not null comment '如 gpt-4o-mini/deepseek-chat',
  model_name varchar(120) not null,
  context_window int not null default 0 comment '上下文窗口 token',
  input_price_per_1k decimal(12,6) not null default 0 comment '输入每千 token 价格',
  output_price_per_1k decimal(12,6) not null default 0 comment '输出每千 token 价格',
  enabled tinyint not null default 1,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  unique key uk_provider_model (provider_id, model_code),
  constraint fk_models_provider foreign key (provider_id) references model_providers(id)
) comment '模型表';

create table api_keys (
  id bigint primary key auto_increment,
  user_id bigint not null,
  provider_id bigint not null,
  key_name varchar(80) not null default 'default',
  encrypted_key text not null comment '加密后的供应商 API Key',
  key_mask varchar(40) not null comment '脱敏展示',
  enabled tinyint not null default 1,
  last_used_at datetime null,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  unique key uk_user_provider_key_name (user_id, provider_id, key_name),
  key idx_api_keys_user (user_id),
  constraint fk_api_keys_user foreign key (user_id) references users(id),
  constraint fk_api_keys_provider foreign key (provider_id) references model_providers(id)
) comment '供应商 API Key 表';

create table platform_tokens (
  id bigint primary key auto_increment,
  user_id bigint not null,
  token_name varchar(80) not null,
  token_hash varchar(255) not null unique comment '平台侧访问 token 哈希',
  token_prefix varchar(20) not null comment '用于展示和定位',
  enabled tinyint not null default 1,
  expires_at datetime null,
  last_used_at datetime null,
  created_at datetime not null default current_timestamp,
  constraint fk_platform_tokens_user foreign key (user_id) references users(id)
) comment '平台访问令牌表';

create table prompt_templates (
  id bigint primary key auto_increment,
  user_id bigint not null,
  title varchar(120) not null,
  content text not null,
  visibility varchar(20) not null default 'private' comment 'private/team/public',
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  key idx_prompt_templates_user (user_id),
  constraint fk_prompt_templates_user foreign key (user_id) references users(id)
) comment 'Prompt 模板表';

create table conversations (
  id bigint primary key auto_increment,
  user_id bigint not null,
  title varchar(200) not null,
  provider_id bigint null,
  model_id bigint null,
  status varchar(20) not null default 'active',
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  key idx_conversations_user_updated (user_id, updated_at),
  constraint fk_conversations_user foreign key (user_id) references users(id),
  constraint fk_conversations_provider foreign key (provider_id) references model_providers(id),
  constraint fk_conversations_model foreign key (model_id) references models(id)
) comment '对话表';

create table messages (
  id bigint primary key auto_increment,
  conversation_id bigint not null,
  role varchar(20) not null comment 'system/user/assistant/tool',
  content longtext not null,
  token_count int not null default 0,
  created_at datetime not null default current_timestamp,
  key idx_messages_conversation_created (conversation_id, created_at),
  constraint fk_messages_conversation foreign key (conversation_id) references conversations(id)
) comment '消息表';

create table usage_events (
  id bigint primary key auto_increment,
  user_id bigint not null,
  provider_id bigint null,
  model_id bigint null,
  conversation_id bigint null,
  request_id varchar(80) not null unique,
  prompt_tokens int not null default 0,
  completion_tokens int not null default 0,
  total_tokens int not null default 0,
  cost_amount decimal(14,6) not null default 0,
  latency_ms int not null default 0,
  success tinyint not null default 1,
  error_message varchar(500) null,
  created_at datetime not null default current_timestamp,
  key idx_usage_user_created (user_id, created_at),
  key idx_usage_provider_created (provider_id, created_at),
  constraint fk_usage_user foreign key (user_id) references users(id),
  constraint fk_usage_provider foreign key (provider_id) references model_providers(id),
  constraint fk_usage_model foreign key (model_id) references models(id),
  constraint fk_usage_conversation foreign key (conversation_id) references conversations(id)
) comment 'Token 用量与费用流水表';

create table quota_accounts (
  id bigint primary key auto_increment,
  user_id bigint not null unique,
  quota_tokens bigint not null default 200000,
  used_tokens bigint not null default 0,
  reset_cycle varchar(20) not null default 'monthly' comment 'none/daily/monthly',
  reset_at datetime null,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  constraint fk_quota_accounts_user foreign key (user_id) references users(id)
) comment '用户 Token 额度账户表';

create table rate_limit_rules (
  id bigint primary key auto_increment,
  scope_type varchar(20) not null comment 'user/provider/model',
  scope_id bigint not null default 0,
  max_requests int not null,
  window_seconds int not null,
  enabled tinyint not null default 1,
  created_at datetime not null default current_timestamp,
  unique key uk_rate_limit_scope (scope_type, scope_id)
) comment '限流规则表';

create table provider_health_events (
  id bigint primary key auto_increment,
  provider_id bigint not null,
  status varchar(20) not null comment 'healthy/degraded/down',
  latency_ms int null,
  error_message varchar(500) null,
  checked_at datetime not null default current_timestamp,
  key idx_provider_health_checked (provider_id, checked_at),
  constraint fk_provider_health_provider foreign key (provider_id) references model_providers(id)
) comment '供应商健康检查表';

create table audit_logs (
  id bigint primary key auto_increment,
  user_id bigint null,
  action varchar(80) not null comment 'login/save_key/chat/delete_prompt 等',
  resource_type varchar(80) null,
  resource_id varchar(80) null,
  ip_address varchar(64) null,
  user_agent varchar(500) null,
  detail json null,
  created_at datetime not null default current_timestamp,
  key idx_audit_user_created (user_id, created_at),
  key idx_audit_action_created (action, created_at),
  constraint fk_audit_user foreign key (user_id) references users(id)
) comment '审计日志表';

insert into roles (code, name) values
  ('admin', '管理员'),
  ('user', '普通用户'),
  ('auditor', '审计员')
on duplicate key update name = values(name);

insert into model_providers (provider_code, provider_name, base_url, protocol) values
  ('openai', 'OpenAI', 'https://api.openai.com/v1', 'openai-compatible'),
  ('anthropic', 'Anthropic', 'https://api.anthropic.com/v1', 'anthropic'),
  ('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1', 'openai-compatible'),
  ('qwen', '通义千问', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'openai-compatible'),
  ('zhipu', '智谱 GLM', 'https://open.bigmodel.cn/api/paas/v4', 'openai-compatible')
on duplicate key update
  provider_name = values(provider_name),
  base_url = values(base_url),
  protocol = values(protocol);

