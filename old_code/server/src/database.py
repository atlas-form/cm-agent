"""
数据库层 — aiosqlite 异步连接池，45张核心表，WAL模式。

跨平台：路径使用 pathlib.Path，DB文件在 server/data/v4.db。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

import aiosqlite

from src.config import DB_PATH, DATA_DIR, PRODUCTS_DIR

logger = logging.getLogger(__name__)

# 全局连接（单连接 + WAL模式对读写并发友好）
_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    """获取全局数据库连接。"""
    global _db
    if _db is None:
        await init_db()
    assert _db is not None
    return _db


@asynccontextmanager
async def get_db_ctx() -> AsyncGenerator[aiosqlite.Connection, None]:
    """数据库连接的上下文管理器。"""
    db = await get_db()
    yield db


async def _fetchone(self, sql: str, params: tuple = ()):
    """aiosqlite没有execute_fetchone，monkey-patch。"""
    rows = await self.execute_fetchall(sql, params)
    return rows[0] if rows else None

# Monkey-patch aiosqlite.Connection
aiosqlite.Connection.execute_fetchone = _fetchone


async def init_db() -> None:
    """初始化数据库：创建连接 + 建表。"""
    global _db
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _create_tables(_db)
    await _db.commit()
    logger.info("Database initialized at %s", DB_PATH)


async def close_db() -> None:
    """关闭数据库连接。"""
    global _db
    if _db:
        await _db.close()
        _db = None


async def _create_tables(db: aiosqlite.Connection) -> None:
    """创建45张核心表。"""

    # ─────────────────────────────────────────────────────────────────────────
    # 原有20张表（保持结构，补充新列）
    # ─────────────────────────────────────────────────────────────────────────

    # 1. users  (+active_agent, +account_role)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            active_agent TEXT DEFAULT 'ops',
            account_role TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 兼容旧库：列不存在时添加
    try:
        await db.execute("ALTER TABLE users ADD COLUMN active_agent TEXT DEFAULT 'ops'")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE users ADD COLUMN account_role TEXT DEFAULT 'general'")
    except Exception:
        pass

    # 2. sessions
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 3. conversations
    await db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT DEFAULT '',
            agent_role TEXT DEFAULT 'ops',
            is_pinned INTEGER DEFAULT 0,
            pinned_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 兼容旧库：置顶字段不存在时添加
    try:
        await db.execute("ALTER TABLE conversations ADD COLUMN is_pinned INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE conversations ADD COLUMN pinned_at TIMESTAMP")
    except Exception:
        pass
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_conversations_user_pin_time
        ON conversations(user_id, is_pinned DESC, updated_at DESC)
    """)

    # 4. messages
    await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
            content TEXT NOT NULL DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_conv
        ON messages(conversation_id, created_at)
    """)

    # 5. agents
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            description TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            config TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 6. skills
    await db.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT DEFAULT '',
            input_schema TEXT DEFAULT '{}',
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 7. skill_runs  (+rating, +feedback_tags)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS skill_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            conversation_id TEXT,
            user_id INTEGER,
            input TEXT DEFAULT '{}',
            output TEXT DEFAULT '{}',
            duration_ms INTEGER DEFAULT 0,
            success INTEGER DEFAULT 1,
            rating INTEGER DEFAULT 0,
            feedback_tags TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        await db.execute("ALTER TABLE skill_runs ADD COLUMN rating INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE skill_runs ADD COLUMN feedback_tags TEXT DEFAULT ''")
    except Exception:
        pass

    # 8. quality_checks
    await db.execute("""
        CREATE TABLE IF NOT EXISTS quality_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT NOT NULL,
            score REAL NOT NULL,
            passed INTEGER NOT NULL,
            dimensions TEXT DEFAULT '{}',
            issues TEXT DEFAULT '[]',
            suggestions TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 9. trust_scores
    await db.execute("""
        CREATE TABLE IF NOT EXISTS trust_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.5,
            trust_level TEXT NOT NULL DEFAULT 'MODERATE',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_trust_role
        ON trust_scores(role, updated_at DESC)
    """)

    # 10. learnings
    await db.execute("""
        CREATE TABLE IF NOT EXISTS learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            source_action TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_learnings_role
        ON learnings(role, confidence DESC)
    """)

    # 11. alerts  (+snoozed_until)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            role TEXT DEFAULT '',
            user_id INTEGER,
            status TEXT DEFAULT 'active',
            dedup_key TEXT DEFAULT '',
            snoozed_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        await db.execute("ALTER TABLE alerts ADD COLUMN snoozed_until TIMESTAMP")
    except Exception:
        pass

    # 12. metrics
    await db.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_type TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            metric_value REAL NOT NULL DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 13. products  (+supplier, +description, +delete_pin_hash, +folder_path)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            sku TEXT DEFAULT '',
            cost_price REAL DEFAULT 0,
            selling_price REAL DEFAULT 0,
            lifecycle_status TEXT DEFAULT 'draft',
            supplier TEXT DEFAULT '',
            description TEXT DEFAULT '',
            delete_pin_hash TEXT DEFAULT NULL,
            folder_path TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        await db.execute("ALTER TABLE products ADD COLUMN supplier TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE products ADD COLUMN delete_pin_hash TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE products ADD COLUMN folder_path TEXT DEFAULT ''")
    except Exception:
        pass

    # 14. product_materials  (+title, +version)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            material_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            version INTEGER DEFAULT 1,
            content TEXT NOT NULL DEFAULT '',
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        await db.execute("ALTER TABLE product_materials ADD COLUMN title TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE product_materials ADD COLUMN version INTEGER DEFAULT 1")
    except Exception:
        pass

    # 15. campaigns
    await db.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            product_id INTEGER,
            budget REAL DEFAULT 0,
            start_date TEXT,
            end_date TEXT,
            status TEXT DEFAULT 'draft',
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 16. workspaces
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            workspace_type TEXT DEFAULT 'general',
            product_id INTEGER,
            campaign_id INTEGER,
            config TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 17. projects
    await db.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            owner_role TEXT DEFAULT 'ops',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 18. tasks
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            owner_role TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            depends_on TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 19. settings
    await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            scope TEXT DEFAULT 'system',
            user_id INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(key, scope, user_id)
        )
    """)

    # 20. background_events (后台事件队列 — 用于推送quality/trust/alert/learning结果)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS background_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            conversation_id TEXT DEFAULT '',
            event_type TEXT NOT NULL,
            data TEXT NOT NULL DEFAULT '{}',
            consumed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_bg_events_user
        ON background_events(user_id, consumed, created_at DESC)
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # 新增25张表
    # ─────────────────────────────────────────────────────────────────────────

    # 21. user_settings — 用户偏好键值对
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            key TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_settings_uid
        ON user_settings(user_id)
    """)

    # 22. conversation_contexts — 对话摘要与事实
    await db.execute("""
        CREATE TABLE IF NOT EXISTS conversation_contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            summary TEXT DEFAULT '',
            facts TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_conv_ctx_cid
        ON conversation_contexts(conversation_id)
    """)

    # 23. role_shared_context — 跨角色共享洞察
    await db.execute("""
        CREATE TABLE IF NOT EXISTS role_shared_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER,
            from_role TEXT NOT NULL,
            to_role TEXT NOT NULL,
            content TEXT NOT NULL,
            visibility TEXT DEFAULT 'team',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_role_shared_to
        ON role_shared_context(to_role, created_at DESC)
    """)

    # 24. skill_quality_reports — 技能聚合质量报告
    await db.execute("""
        CREATE TABLE IF NOT EXISTS skill_quality_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            avg_rating REAL DEFAULT 0,
            total_runs INTEGER DEFAULT 0,
            top_tags TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_skill_quality_name
        ON skill_quality_reports(skill_name)
    """)

    # 25. agent_hires — Agent市场雇佣记录
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_hires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            agent_name TEXT NOT NULL,
            hired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active'
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_hires_user
        ON agent_hires(user_id, status)
    """)

    # 26. product_suggestions — 角色对产品的建议
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            role TEXT NOT NULL,
            suggestion_type TEXT DEFAULT 'improvement',
            content TEXT NOT NULL,
            dismissed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_prod_suggestions_pid
        ON product_suggestions(product_id, dismissed)
    """)

    # 27. product_assets — 产品文件上传
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            filename TEXT NOT NULL,
            original_name TEXT DEFAULT '',
            file_path TEXT NOT NULL,
            file_type TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            source_rel_path TEXT DEFAULT '',
            source_sha1 TEXT DEFAULT '',
            import_run_id INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_prod_assets_pid
        ON product_assets(product_id)
    """)
    try:
        await db.execute("ALTER TABLE product_assets ADD COLUMN original_name TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE product_assets ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE product_assets ADD COLUMN source_rel_path TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE product_assets ADD COLUMN source_sha1 TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE product_assets ADD COLUMN import_run_id INTEGER DEFAULT 0")
    except Exception:
        pass
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_prod_assets_source_hash
        ON product_assets(product_id, source_sha1)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_prod_assets_source_unique
        ON product_assets(product_id, source_rel_path, source_sha1)
        WHERE source_rel_path IS NOT NULL
          AND source_rel_path != ''
          AND source_sha1 IS NOT NULL
          AND source_sha1 != ''
    """)

    # 28. product_manifest — 产品素材清单
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            asset_summary TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_prod_manifest_pid
        ON product_manifest(product_id)
    """)

    # 28.5 local_material_import_runs — 本地素材目录导入任务
    await db.execute("""
        CREATE TABLE IF NOT EXISTS local_material_import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            folder_path TEXT NOT NULL DEFAULT '',
            recursive INTEGER DEFAULT 1,
            status TEXT DEFAULT 'running',
            total_files INTEGER DEFAULT 0,
            imported_assets INTEGER DEFAULT 0,
            imported_knowledge INTEGER DEFAULT 0,
            skipped_files INTEGER DEFAULT 0,
            failed_files INTEGER DEFAULT 0,
            options TEXT DEFAULT '{}',
            summary TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_local_material_import_runs_user
        ON local_material_import_runs(user_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_local_material_import_runs_product
        ON local_material_import_runs(product_id, created_at DESC)
    """)

    # 28.6 local_material_import_items — 本地素材目录导入明细
    await db.execute("""
        CREATE TABLE IF NOT EXISTS local_material_import_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES local_material_import_runs(id) ON DELETE CASCADE,
            relative_path TEXT NOT NULL DEFAULT '',
            absolute_path TEXT NOT NULL DEFAULT '',
            file_type TEXT DEFAULT 'docs',
            file_ext TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            source_sha1 TEXT DEFAULT '',
            asset_id INTEGER DEFAULT 0,
            knowledge_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'imported',
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_local_material_import_items_run
        ON local_material_import_items(run_id, status)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_local_material_import_items_unique
        ON local_material_import_items(run_id, relative_path, source_sha1)
        WHERE relative_path IS NOT NULL
          AND relative_path != ''
          AND source_sha1 IS NOT NULL
          AND source_sha1 != ''
    """)

    # 29. trash — 软删除，30天过期
    await db.execute("""
        CREATE TABLE IF NOT EXISTS trash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_table TEXT NOT NULL,
            original_id INTEGER NOT NULL,
            data TEXT NOT NULL DEFAULT '{}',
            deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_trash_expires
        ON trash(expires_at)
    """)

    # 30. workspace_conversations — Workspace专属对话
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            conversation_id TEXT NOT NULL,
            process_state TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_conv_wid
        ON workspace_conversations(workspace_id)
    """)

    # 30.5 team_memberships — 工作区协作成员与角色
    await db.execute("""
        CREATE TABLE IF NOT EXISTS team_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'member', 'viewer')),
            invited_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, user_id)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_memberships_workspace
        ON team_memberships(workspace_id, role)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_memberships_user
        ON team_memberships(user_id, workspace_id)
    """)

    # 31. workspace_memory — Workspace范围知识
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            role TEXT DEFAULT '',
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            memory_type TEXT DEFAULT 'fact',
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_memory_wid
        ON workspace_memory(workspace_id, role)
    """)

    # 32. workspace_tasks — Workspace任务（含验收标准）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            owner_role TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            depends_on TEXT DEFAULT '',
            acceptance_criteria TEXT DEFAULT '',
            result TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_tasks_wid
        ON workspace_tasks(workspace_id, status)
    """)

    # 32.1 workspace_flow_runs — 多Agent执行编排运行记录
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_flow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            goal TEXT NOT NULL,
            requirements TEXT DEFAULT '[]',
            status TEXT DEFAULT 'planned',
            source TEXT DEFAULT 'manual',
            summary TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_flow_runs_wid
        ON workspace_flow_runs(workspace_id, status)
    """)

    # 32.2 workspace_flow_tasks — 执行编排任务快照 + 验收结果
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_flow_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES workspace_flow_runs(id),
            workspace_task_id INTEGER NOT NULL REFERENCES workspace_tasks(id),
            seq INTEGER DEFAULT 0,
            owner_role TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            acceptance_score REAL DEFAULT 0,
            acceptance_passed INTEGER DEFAULT 0,
            acceptance_notes TEXT DEFAULT '',
            execution_notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_flow_tasks_run
        ON workspace_flow_tasks(run_id, status)
    """)

    # 32.3 workspace_action_logs — 自动执行动作日志
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES workspace_flow_runs(id),
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            task_id INTEGER NOT NULL REFERENCES workspace_tasks(id),
            action_type TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            status TEXT DEFAULT 'done',
            action_key TEXT DEFAULT '',
            attempts INTEGER DEFAULT 0,
            approved_by INTEGER,
            approved_at TIMESTAMP,
            result TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_action_logs_run
        ON workspace_action_logs(run_id, task_id)
    """)
    try:
        await db.execute("ALTER TABLE workspace_action_logs ADD COLUMN action_key TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE workspace_action_logs ADD COLUMN attempts INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE workspace_action_logs ADD COLUMN approved_by INTEGER")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE workspace_action_logs ADD COLUMN approved_at TIMESTAMP")
    except Exception:
        pass
    await db.execute("DROP INDEX IF EXISTS idx_ws_action_logs_key")
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ws_action_logs_key
        ON workspace_action_logs(action_key)
        WHERE action_key IS NOT NULL AND action_key != ''
    """)

    # 32.4 audit_logs — 高风险操作统一审计日志
    await db.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER REFERENCES workspaces(id),
            actor_user_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'success',
            reason TEXT DEFAULT '',
            request_id TEXT DEFAULT '',
            run_id INTEGER,
            action_log_id INTEGER,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_workspace
        ON audit_logs(workspace_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_actor
        ON audit_logs(actor_user_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_action
        ON audit_logs(action, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_run
        ON audit_logs(run_id, created_at DESC)
    """)

    # 33. handoff_queue — 角色间任务移交
    await db.execute("""
        CREATE TABLE IF NOT EXISTS handoff_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_role TEXT NOT NULL,
            to_role TEXT NOT NULL,
            context TEXT DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_handoff_to_role
        ON handoff_queue(to_role, status)
    """)

    # 34. project_tasks — 项目任务（增强版，含验收标准）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS project_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            owner_role TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            depends_on TEXT DEFAULT '',
            acceptance_criteria TEXT DEFAULT '',
            result TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_proj_tasks_pid
        ON project_tasks(project_id, status)
    """)

    # 35. project_facts — 项目级事实
    await db.execute("""
        CREATE TABLE IF NOT EXISTS project_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            fact_type TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            confidence REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_proj_facts_pid
        ON project_facts(project_id, fact_type)
    """)

    # 36. artifacts — 生成的文档/产出物
    await db.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_id INTEGER,
            artifact_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_artifacts_user
        ON artifacts(user_id, artifact_type)
    """)

    # 37. profiles — 用户人设/画像
    await db.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            persona TEXT DEFAULT '',
            traits TEXT DEFAULT '{}',
            interests TEXT DEFAULT '{}',
            platforms TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_uid
        ON profiles(user_id)
    """)

    # 38. user_panorama — 用户全景快照
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_panorama (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            snapshot TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_panorama_uid
        ON user_panorama(user_id)
    """)

    # 39. user_mental_model — 用户专业度/决策风格
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_mental_model (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            expertise_level TEXT DEFAULT 'intermediate',
            decision_style TEXT DEFAULT 'balanced',
            risk_tolerance TEXT DEFAULT 'moderate',
            goals TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mental_model_uid
        ON user_mental_model(user_id)
    """)

    # 40. global_facts — 跨对话事实
    await db.execute("""
        CREATE TABLE IF NOT EXISTS global_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL DEFAULT '',
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_facts_user
        ON global_facts(user_id, fact_key)
    """)

    # 41. agent_episodes — 已保存的对话片段
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agent_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            topic TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            messages TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_user_role
        ON agent_episodes(user_id, role, created_at DESC)
    """)

    # 42. knowledge_base — 持久化知识库
    await db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT '',
            access_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_kb_role_cat
        ON knowledge_base(role, category, confidence DESC)
    """)

    # 43. learned_keywords — 角色专属关键词权重
    await db.execute("""
        CREATE TABLE IF NOT EXISTS learned_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            keyword TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_keywords_role
        ON learned_keywords(role, weight DESC)
    """)

    # 44. confidence_calibration — 预测追踪
    await db.execute("""
        CREATE TABLE IF NOT EXISTS confidence_calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            prediction TEXT DEFAULT '',
            actual TEXT DEFAULT '',
            delta REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_role
        ON confidence_calibration(role, created_at DESC)
    """)

    # 45a. store_autopilot_runs — 自动驾驶执行历史
    await db.execute("""
        CREATE TABLE IF NOT EXISTS store_autopilot_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            run_type TEXT NOT NULL DEFAULT 'manual',
            status TEXT DEFAULT 'pending',
            result TEXT DEFAULT '{}',
            risk_snapshot TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_autopilot_runs_user
        ON store_autopilot_runs(user_id, created_at DESC)
    """)

    # 45b. store_autopilot_schedules — 定时自动驾驶
    await db.execute("""
        CREATE TABLE IF NOT EXISTS store_autopilot_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            schedule_type TEXT DEFAULT 'daily',
            cron_expr TEXT DEFAULT '0 9 * * *',
            enabled INTEGER DEFAULT 1,
            last_run_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_autopilot_sched_user
        ON store_autopilot_schedules(user_id, schedule_type)
    """)

    # 45c. store_daily_reports — 每日绩效报告
    await db.execute("""
        CREATE TABLE IF NOT EXISTS store_daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            report_date TEXT NOT NULL,
            data TEXT DEFAULT '{}',
            archived INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_reports_user
        ON store_daily_reports(user_id, report_date DESC)
    """)

    # 46. product_knowledge — 产品知识库（文件提取 + 对话沉淀）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            content_type TEXT NOT NULL DEFAULT 'text',
            content TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT DEFAULT '',
            confidence REAL DEFAULT 0.8,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_product_knowledge_pid
        ON product_knowledge(product_id, confidence DESC)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_product_knowledge_dedup
        ON product_knowledge(product_id, content)
    """)

    # 47. product_pin_lock — PIN暴力破解保护
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_pin_lock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL UNIQUE REFERENCES products(id),
            failed_attempts INTEGER DEFAULT 0,
            locked_until TIMESTAMP DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 48. knowledge_embeddings — 向量语义搜索存储
    await db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            embedding TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_emb_key
        ON knowledge_embeddings(source_type, source_id, content)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_emb_type
        ON knowledge_embeddings(source_type)
    """)

    # 49. platform_connections — 电商平台 API 凭证（加密存储）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS platform_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            platform TEXT NOT NULL,
            app_key TEXT DEFAULT '',
            app_secret_enc TEXT DEFAULT '',
            access_token_enc TEXT DEFAULT '',
            refresh_token_enc TEXT DEFAULT '',
            token_expires_at TIMESTAMP,
            shop_id TEXT DEFAULT '',
            shop_name TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            last_tested_at TIMESTAMP,
            test_result TEXT DEFAULT '',
            extra TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_conn_user_platform
        ON platform_connections(user_id, platform)
    """)

    # 49.1 channel_connections — 多渠道触达连接（企微/飞书/TG/Discord）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS channel_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            channel TEXT NOT NULL,
            webhook_url TEXT DEFAULT '',
            bot_token_enc TEXT DEFAULT '',
            chat_id TEXT DEFAULT '',
            secret_enc TEXT DEFAULT '',
            extra_credentials TEXT DEFAULT '{}',
            enabled INTEGER DEFAULT 1,
            last_tested_at TIMESTAMP,
            last_error TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_conn_user_channel
        ON channel_connections(user_id, channel)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_channel_conn_user_updated
        ON channel_connections(user_id, updated_at DESC)
    """)

    # 49.2 channel_delivery_logs — 多渠道触达发送日志
    await db.execute("""
        CREATE TABLE IF NOT EXISTS channel_delivery_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            channel TEXT NOT NULL,
            target TEXT DEFAULT '',
            title TEXT DEFAULT '',
            content TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'success',
            response_json TEXT DEFAULT '{}',
            error_message TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_channel_delivery_user_created
        ON channel_delivery_logs(user_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_channel_delivery_user_channel
        ON channel_delivery_logs(user_id, channel, created_at DESC)
    """)

    # 50. store_metrics — 平台真实指标数据（CSV导入 / API拉取）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS store_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            platform TEXT NOT NULL DEFAULT 'general',
            metric_date TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            metric_value REAL NOT NULL DEFAULT 0,
            dimension_key TEXT DEFAULT '',
            dimension_value TEXT DEFAULT '',
            source TEXT DEFAULT 'import',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_store_metrics_user_date
        ON store_metrics(user_id, metric_date DESC, platform)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_store_metrics_dedup
        ON store_metrics(user_id, platform, metric_date, metric_key, dimension_key, dimension_value)
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS long_running_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            task_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            total_steps INTEGER NOT NULL DEFAULT 100,
            current_step TEXT DEFAULT '',
            result_json TEXT DEFAULT NULL,
            error_message TEXT DEFAULT NULL,
            role TEXT DEFAULT '',
            input_params TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP DEFAULT NULL,
            completed_at TIMESTAMP DEFAULT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_long_running_tasks_user
        ON long_running_tasks(user_id, status, created_at DESC)
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            platform TEXT DEFAULT '',
            url TEXT DEFAULT '',
            price REAL,
            rating REAL,
            monthly_sales TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            search_results_json TEXT DEFAULT NULL,
            last_searched_at TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_product_competitors_product
        ON product_competitors(product_id, created_at DESC)
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS execution_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            action_type TEXT NOT NULL,
            description TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}',
            expected_impact TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            executed_at TIMESTAMP DEFAULT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_execution_queue_user
        ON execution_queue(user_id, status, created_at DESC)
    """)

    # 50.1 tool_approval_queue — chat 工具执行审批池
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tool_approval_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            conversation_id TEXT DEFAULT '',
            request_role TEXT DEFAULT '',
            skill_name TEXT NOT NULL,
            tool_call_id TEXT DEFAULT '',
            risk_level TEXT NOT NULL DEFAULT 'L1',
            args_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            reason TEXT DEFAULT '',
            action_key TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            approved_by INTEGER,
            approved_at TIMESTAMP DEFAULT NULL,
            executed_at TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tool_approval_workspace
        ON tool_approval_queue(workspace_id, status, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tool_approval_user
        ON tool_approval_queue(user_id, status, created_at DESC)
    """)
    await db.execute("DROP INDEX IF EXISTS idx_tool_approval_action_key")
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_approval_action_key
        ON tool_approval_queue(action_key)
        WHERE action_key IS NOT NULL AND action_key != ''
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS competitor_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES product_competitors(id),
            product_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id),
            change_type TEXT NOT NULL,
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_competitor_changes_product
        ON competitor_changes(product_id, detected_at DESC)
    """)

    # 51. package_registry — 通用化包实时状态
    await db.execute("""
        CREATE TABLE IF NOT EXISTS package_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_type TEXT NOT NULL,
            package_id TEXT NOT NULL,
            package_name TEXT DEFAULT '',
            version TEXT DEFAULT '0.1.0',
            status TEXT NOT NULL DEFAULT 'active',
            manifest_path TEXT DEFAULT '',
            updated_by INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_package_registry_unique
        ON package_registry(package_type, package_id)
    """)

    # 52. package_state_history — 包状态变更历史（支持回滚）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS package_state_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_type TEXT NOT NULL,
            package_id TEXT NOT NULL,
            from_status TEXT NOT NULL DEFAULT 'active',
            to_status TEXT NOT NULL DEFAULT 'active',
            reason TEXT DEFAULT '',
            changed_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_package_state_history_pkg
        ON package_state_history(package_type, package_id, created_at DESC)
    """)


    # 53. workspace_run_package_locks — 执行编排的包版本锁快照（防止运行期漂移）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_run_package_locks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES workspace_flow_runs(id),
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            package_type TEXT NOT NULL,
            package_id TEXT NOT NULL,
            locked_version TEXT DEFAULT '0.1.0',
            locked_status TEXT DEFAULT 'active',
            required INTEGER DEFAULT 1,
            lock_source TEXT DEFAULT '',
            manifest_path TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ws_run_pkg_lock_unique
        ON workspace_run_package_locks(run_id, package_type, package_id)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ws_run_pkg_lock_run
        ON workspace_run_package_locks(run_id, created_at)
    """)

    # 54. kernel_fix_apply_logs — 内核通用化修复执行审计日志
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kernel_fix_apply_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            package_type TEXT DEFAULT '',
            package_id TEXT DEFAULT '',
            status TEXT DEFAULT 'unknown',
            reason TEXT DEFAULT '',
            payload TEXT DEFAULT '{}',
            result TEXT DEFAULT '{}',
            changed_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_kernel_fix_apply_logs_created
        ON kernel_fix_apply_logs(created_at DESC)
    """)

    # 55. teaching_submissions — 学生提交给教师评阅的对话清单
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'submitted',
            note TEXT DEFAULT '',
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(conversation_id, student_user_id, teacher_user_id)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_submissions_teacher
        ON teaching_submissions(teacher_user_id, status, submitted_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_submissions_student
        ON teaching_submissions(student_user_id, submitted_at DESC)
    """)

    # 56. teaching_evaluations — 教师评价记录（支持评分量表）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL REFERENCES teaching_submissions(id),
            conversation_id TEXT NOT NULL,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            message_id INTEGER,
            score REAL NOT NULL DEFAULT 0,
            feedback TEXT DEFAULT '',
            rubric_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_evals_submission
        ON teaching_evaluations(submission_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_evals_student
        ON teaching_evaluations(student_user_id, created_at DESC)
    """)

    # 57. teaching_classes — 教师创建的班级组织
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(teacher_user_id, name)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_classes_teacher
        ON teaching_classes(teacher_user_id, status, created_at DESC)
    """)

    # 58. teaching_class_members — 班级学生成员关系
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_class_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id),
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(class_id, student_user_id)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_class_members_class
        ON teaching_class_members(class_id, joined_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_class_members_student
        ON teaching_class_members(student_user_id, class_id)
    """)

    # 59. teaching_assignments — 班级作业定义
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id),
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            due_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_assignments_class
        ON teaching_assignments(class_id, status, due_at)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_assignments_teacher
        ON teaching_assignments(teacher_user_id, created_at DESC)
    """)

    # 60. teaching_assignment_submissions — 作业提交与会话评阅关联
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_assignment_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL REFERENCES teaching_assignments(id),
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id),
            teaching_submission_id INTEGER NOT NULL REFERENCES teaching_submissions(id),
            conversation_id TEXT NOT NULL,
            student_user_id INTEGER NOT NULL REFERENCES users(id),
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'submitted',
            note TEXT DEFAULT '',
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(assignment_id, student_user_id)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_assignment_submissions_assignment
        ON teaching_assignment_submissions(assignment_id, submitted_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_assignment_submissions_teacher
        ON teaching_assignment_submissions(teacher_user_id, assignment_id, status)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_assignment_submissions_student
        ON teaching_assignment_submissions(student_user_id, submitted_at DESC)
    """)

    # 61. teaching_templates — 教师实训模板（班级/作业/评分量表）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            template_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            payload_json TEXT DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(teacher_user_id, template_type, name)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_templates_owner_type
        ON teaching_templates(teacher_user_id, template_type, status, created_at DESC)
    """)

    # 62. teaching_template_versions — 模板历史版本（治理/回滚）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_template_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL REFERENCES teaching_templates(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            template_type TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            change_type TEXT NOT NULL DEFAULT 'update',
            change_note TEXT DEFAULT '',
            source_version_id INTEGER,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            payload_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(template_id, version_no)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_template_versions_template
        ON teaching_template_versions(template_id, version_no DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_template_versions_teacher
        ON teaching_template_versions(teacher_user_id, created_at DESC)
    """)

    # 63. teaching_template_usage_logs — 模板使用日志（统计/运营洞察）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_template_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL REFERENCES teaching_templates(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            template_type TEXT NOT NULL,
            usage_scene TEXT NOT NULL,
            target_id INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_template_usage_logs_template
        ON teaching_template_usage_logs(template_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_template_usage_logs_teacher
        ON teaching_template_usage_logs(teacher_user_id, created_at DESC)
    """)


    # 64. teaching_intervention_actions — 课堂干预执行记录（治理闭环）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_intervention_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            intervention_code TEXT NOT NULL,
            intervention_title TEXT DEFAULT '',
            intervention_severity TEXT DEFAULT 'medium',
            assignment_id INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_intervention_actions_class
        ON teaching_intervention_actions(class_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_intervention_actions_code
        ON teaching_intervention_actions(class_id, intervention_code, created_at DESC)
    """)


    # 65. teaching_class_goals — 课堂目标管理（长期教学闭环）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_class_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            goal_code TEXT NOT NULL,
            goal_name TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            target_value REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT DEFAULT '',
            due_at TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_class_goals_class
        ON teaching_class_goals(class_id, created_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_class_goals_teacher
        ON teaching_class_goals(teacher_user_id, status, created_at DESC)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teaching_class_goals_unique_code
        ON teaching_class_goals(class_id, teacher_user_id, goal_code)
    """)


    # 66. teaching_class_goal_snapshots — 课堂目标历史快照（跨周期趋势与效果评估）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_class_goal_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id) ON DELETE CASCADE,
            goal_id INTEGER NOT NULL REFERENCES teaching_class_goals(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            goal_code TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            target_value REAL NOT NULL DEFAULT 0,
            current_value REAL NOT NULL DEFAULT 0,
            progress_ratio REAL NOT NULL DEFAULT 0,
            is_achieved INTEGER NOT NULL DEFAULT 0,
            goal_status TEXT NOT NULL DEFAULT 'active',
            due_at TEXT DEFAULT '',
            snapshot_date TEXT NOT NULL,
            source TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_goal_snapshots_goal
        ON teaching_class_goal_snapshots(goal_id, snapshot_date DESC, id DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_goal_snapshots_class
        ON teaching_class_goal_snapshots(class_id, teacher_user_id, snapshot_date DESC)
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teaching_goal_snapshots_unique_daily
        ON teaching_class_goal_snapshots(goal_id, snapshot_date)
    """)


    # 67. teaching_goal_term_archives — 目标跨学期归档（长期治理骨架）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_goal_term_archives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            term_code TEXT NOT NULL,
            term_name TEXT DEFAULT '',
            term_start TEXT DEFAULT '',
            term_end TEXT DEFAULT '',
            note TEXT DEFAULT '',
            archive_json TEXT NOT NULL DEFAULT '{}',
            snapshot_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teaching_goal_term_archives_unique
        ON teaching_goal_term_archives(class_id, teacher_user_id, term_code)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_goal_term_archives_class
        ON teaching_goal_term_archives(class_id, teacher_user_id, snapshot_date DESC)
    """)

    # 68. teaching_intervention_experiments — 干预实验编排（计划/状态/复盘）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teaching_intervention_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES teaching_classes(id) ON DELETE CASCADE,
            teacher_user_id INTEGER NOT NULL REFERENCES users(id),
            experiment_code TEXT NOT NULL,
            intervention_code TEXT NOT NULL,
            intervention_title TEXT DEFAULT '',
            strategy_mode TEXT NOT NULL DEFAULT 'experiment',
            target_metric TEXT NOT NULL DEFAULT 'submission_rate',
            recommended_variant TEXT DEFAULT 'A',
            variant_plan_json TEXT NOT NULL DEFAULT '[]',
            window_days INTEGER NOT NULL DEFAULT 14,
            status TEXT NOT NULL DEFAULT 'planned',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teaching_intervention_experiments_unique
        ON teaching_intervention_experiments(class_id, teacher_user_id, experiment_code)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_intervention_experiments_status
        ON teaching_intervention_experiments(class_id, teacher_user_id, status, updated_at DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_teaching_intervention_experiments_code
        ON teaching_intervention_experiments(class_id, teacher_user_id, intervention_code, updated_at DESC)
    """)

    # 69. user_skill_packs — 账号级自建技能包（草稿/发布/停用）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_skill_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            skill_code TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'custom',
            system_prompt TEXT DEFAULT '',
            prompt_template TEXT DEFAULT '',
            input_schema TEXT NOT NULL DEFAULT '{}',
            output_mode TEXT NOT NULL DEFAULT 'text',
            temperature REAL NOT NULL DEFAULT 0.3,
            model TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            version_no INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_skill_packs_unique
        ON user_skill_packs(user_id, skill_code)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_skill_packs_status
        ON user_skill_packs(user_id, status, updated_at DESC)
    """)

    # 70. user_skill_pack_versions — 自建技能包版本快照（回滚依据）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_skill_pack_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_skill_pack_id INTEGER NOT NULL REFERENCES user_skill_packs(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            skill_code TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            change_type TEXT NOT NULL DEFAULT 'update',
            note TEXT DEFAULT '',
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_skill_pack_versions_pack
        ON user_skill_pack_versions(user_skill_pack_id, id DESC)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_skill_pack_versions_user
        ON user_skill_pack_versions(user_id, skill_code, id DESC)
    """)
    # ═══════════════════════════════════════════════════════════════════════════
# 种子数据：8个Agent
# ═══════════════════════════════════════════════════════════════════════════

_SEED_AGENTS = [
    ("ops", "运营专家", "ops", "资深电商运营经理，精通全平台流量运营与转化提升", "🎯"),
    ("data", "数据分析师", "data", "资深电商数据分析师，精通数据驱动决策", "📊"),
    ("service", "客服主管", "service", "万级工单处理经验，熟悉各平台售后政策", "🎧"),
    ("design", "视觉总监", "design", "精通全平台视觉规范与转化型设计", "🎨"),
    ("accounting", "财务分析师", "accounting", "精通电商成本结构与利润模型", "💰"),
    ("engineering", "技术架构师", "engineering", "全栈开发 + 电商系统架构经验", "⚙️"),
    ("web", "建站专员", "web", "精通SEO与店铺装修优化", "🌐"),
    ("creative", "创意总监", "creative", "精通短视频脚本与种草内容创作", "✨"),
]


async def seed_agents(db: aiosqlite.Connection) -> None:
    """初始化8个Agent种子数据。"""
    for name, display_name, role, desc, avatar in _SEED_AGENTS:
        await db.execute(
            """
            INSERT OR IGNORE INTO agents (name, display_name, role, description, avatar)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, display_name, role, desc, avatar),
        )
    await db.commit()


