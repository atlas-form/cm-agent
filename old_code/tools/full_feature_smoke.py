from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "server" / "data" / "v4.db"


@dataclass
class StepResult:
    name: str
    method: str
    path: str
    status: int
    ok: bool
    elapsed_ms: int
    detail: str = ""


class SmokeRunner:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.results: list[StepResult] = []

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        expected: Iterable[int] = (200,),
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        timeout: int = 40,
    ) -> requests.Response | None:
        url = f"{self.base_url}{path}"
        t0 = time.time()
        try:
            resp = self.session.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                files=files,
                data=data,
                timeout=timeout,
            )
            elapsed = int((time.time() - t0) * 1000)
            ok = resp.status_code in set(expected)
            detail = ""
            if not ok:
                body = (resp.text or "")[:300].replace("\n", " ")
                detail = f"unexpected_status body={body}"
            self.results.append(
                StepResult(name=name, method=method, path=path, status=resp.status_code, ok=ok, elapsed_ms=elapsed, detail=detail)
            )
            print(f"[{ 'PASS' if ok else 'FAIL' }] {method:<6} {path:<72} -> {resp.status_code} ({elapsed}ms)")
            return resp
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.time() - t0) * 1000)
            detail = f"exception={exc}"
            self.results.append(
                StepResult(name=name, method=method, path=path, status=0, ok=False, elapsed_ms=elapsed, detail=detail)
            )
            print(f"[FAIL] {method:<6} {path:<72} -> {detail}")
            return None

    def ensure_admin(self, email: str) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET is_admin = 1, account_role = 'admin' WHERE email = ?",
                (email,),
            )
            conn.commit()

    def summary(self) -> dict[str, Any]:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.ok)
        failed = total - passed
        return {
            "base_url": self.base_url,
            "total": total,
            "passed": passed,
            "failed": failed,
            "results": [r.__dict__ for r in self.results],
        }


def _must_json(resp: requests.Response | None) -> dict[str, Any]:
    if resp is None:
        return {}
    try:
        return resp.json() if resp.content else {}
    except Exception:
        return {}


def _relogin_and_set_token(runner: SmokeRunner, email: str, password: str) -> str:
    r = runner.call(
        "login_refresh",
        "POST",
        "/api/auth/login",
        expected=(200,),
        json_body={"email": email, "password": password},
    )
    token = _must_json(r).get("token", "")
    if token:
        runner.session.headers.update({"Authorization": f"Bearer {token}"})
    return str(token)


def _wait_task_terminal(runner: SmokeRunner, task_id: int, timeout_seconds: int = 120) -> None:
    """轮询任务直到进入 completed/failed，避免直接调用流接口导致客户端超时。"""
    deadline = time.time() + max(5, int(timeout_seconds))
    url = f"{runner.base_url}/api/tasks/{task_id}"
    while time.time() < deadline:
        try:
            resp = runner.session.get(url, timeout=20)
            if resp.status_code != 200:
                return
            payload = resp.json() if resp.content else {}
            status = str(payload.get("status") or "").strip().lower()
            if status in {"completed", "failed"}:
                return
        except Exception:
            return
        time.sleep(2)


def _record_check(runner: SmokeRunner, name: str, path: str, ok: bool, detail: str = "") -> None:
    """记录非HTTP类的业务断言步骤，避免仅靠200状态码产生假阳性。"""
    msg = detail.strip()
    runner.results.append(
        StepResult(
            name=name,
            method="CHECK",
            path=path,
            status=200 if ok else 0,
            ok=bool(ok),
            elapsed_ms=0,
            detail="" if ok else (msg or "check_failed"),
        )
    )
    print(f"[{'PASS' if ok else 'FAIL'}] CHECK  {path:<72} -> {msg or ('ok' if ok else 'check_failed')}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full feature smoke test")
    parser.add_argument(
        "--base-url",
        default=BASE_URL,
        help="Target server base URL, e.g. http://127.0.0.1:8210",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base_url = str(args.base_url or BASE_URL).rstrip("/")
    print(f"[INFO] smoke_target={base_url}")
    runner = SmokeRunner(base_url)

    # 0) 前端入口可访问性与关键DOM锚点
    r_root = runner.call("frontend_root", "GET", "/", expected=(200,))
    root_html = (r_root.text if r_root is not None else "")
    for marker in (
        "id=\"team-chat-view\"",
        "id=\"team-chat-input\"",
        "id=\"chat-vision-degrade-banner\"",
        "id=\"chat-advanced-panel\"",
    ):
        if marker not in root_html:
            print(f"[FAIL] frontend marker missing: {marker}")
            runner.results.append(
                StepResult(name="frontend_marker", method="GET", path="/", status=r_root.status_code if r_root else 0, ok=False, elapsed_ms=0, detail=f"missing={marker}")
            )
    runner.call("frontend_pro", "GET", "/pro", expected=(200,))
    runner.call("health", "GET", "/api/health", expected=(200,))

    # 1) 账号 + 登录
    email = f"fullsmoke_{uuid.uuid4().hex[:8]}@example.com"
    password = "SmokePass123!"
    runner.call(
        "register",
        "POST",
        "/api/auth/register",
        expected=(200,),
        json_body={"email": email, "password": password, "name": "Full Smoke"},
    )
    r_login = runner.call(
        "login",
        "POST",
        "/api/auth/login",
        expected=(200,),
        json_body={"email": email, "password": password},
    )
    token = _must_json(r_login).get("token", "")
    if not token:
        print("[FAIL] login token missing")
        return 2
    runner.session.headers.update({"Authorization": f"Bearer {token}"})

    # 提升为admin，并重新登录刷新 token claims
    runner.ensure_admin(email)
    if not _relogin_and_set_token(runner, email, password):
        print("[FAIL] login refresh token missing")
        return 2

    # 2) 用户/基础能力
    runner.call("auth_me", "GET", "/api/auth/me")
    runner.call("user_me", "GET", "/api/user/me")
    runner.call("user_overview", "GET", "/api/user/overview")
    runner.call("agents", "GET", "/api/agents")
    runner.call("agents_active", "GET", "/api/agents/active")
    r_agents = runner.call("admin_agents", "GET", "/api/admin/agents")
    runner.call("skills", "GET", "/api/skills")
    runner.call("skills_quality", "GET", "/api/skills/quality")
    runner.call("skills_packs", "GET", "/api/skills/packs")
    runner.call("capabilities_catalog", "GET", "/api/capabilities/catalog")

    agent_items = _must_json(r_agents).get("agents") or _must_json(r_agents).get("items") or []
    if agent_items:
        name = str(agent_items[0].get("name") or "").strip()
        if name:
            runner.call("activate_agent", "POST", "/api/agents/activate", json_body={"agent_name": name})

    # 3) 聊天链路（含附件）
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("这是全链路烟测附件。\n包含商品卖点：轻便、低噪、续航长。")
        tmp_text = Path(f.name)

    with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as f:
        # 1x1 PNG，用于验证图片上传与视觉解析链路
        f.write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+kvwAAAABJRU5ErkJggg=="))
        tmp_image = Path(f.name)

    try:
        with open(tmp_text, "rb") as fd:
            r_attach = runner.call(
                "chat_attachment_upload",
                "POST",
                "/api/chat/attachments/upload",
                expected=(200,),
                files={"file": (tmp_text.name, fd, "text/plain")},
            )
        attachment_id = str((_must_json(r_attach).get("attachment") or {}).get("id") or "")

        with open(tmp_image, "rb") as fd:
            r_image_attach = runner.call(
                "chat_attachment_image_upload",
                "POST",
                "/api/chat/attachments/upload",
                expected=(200,),
                files={"file": (tmp_image.name, fd, "image/png")},
            )

        image_attachment = (_must_json(r_image_attach).get("attachment") or {}) if r_image_attach is not None else {}
        image_attachment_id = str(image_attachment.get("id") or "")
        vision_engine = str(image_attachment.get("vision_engine") or "")
        vision_fallback_reason = str(image_attachment.get("vision_fallback_reason") or "")
        vision_warning = str(image_attachment.get("vision_warning") or "")
        vision_is_degraded = bool(image_attachment.get("vision_is_degraded"))

        _record_check(
            runner,
            "chat_image_vision_fields_presence",
            "/api/chat/attachments/upload",
            all(
                key in image_attachment
                for key in ("vision_engine", "vision_fallback_reason", "vision_warning", "vision_is_degraded")
            ),
            detail=(
                "keys="
                + ",".join(
                    sorted(
                        [k for k in image_attachment.keys() if k.startswith("vision_")] + ["vision_is_degraded"]
                    )
                )
            ),
        )

        _record_check(
            runner,
            "chat_image_vision_engine_check",
            "/api/chat/attachments/upload",
            vision_engine in {"doubao", "ocr-fallback"},
            detail=f"vision_engine={vision_engine}",
        )

        if vision_is_degraded:
            _record_check(
                runner,
                "chat_image_fallback_reason_check",
                "/api/chat/attachments/upload",
                bool(vision_fallback_reason.strip()),
                detail=f"vision_fallback_reason={vision_fallback_reason}",
            )
            # 兼容不同降级原因的提示文案：
            # - config_missing / network 类通常包含“豆包+OCR+兜底”
            # - image_too_small 可能仅提示“分辨率过低+OCR+兜底”
            reason_key = vision_fallback_reason.strip().lower()
            if reason_key == "image_too_small":
                warning_ok = ("OCR" in vision_warning) and ("兜底" in vision_warning) and (
                    ("分辨率" in vision_warning) or ("最短边" in vision_warning)
                )
            else:
                warning_ok = ("OCR" in vision_warning) and ("兜底" in vision_warning)
            _record_check(
                runner,
                "chat_image_fallback_warning_check",
                "/api/chat/attachments/upload",
                warning_ok,
                detail=f"vision_warning={vision_warning}",
            )

        chat_payload = {
            "message": "请给我一个简短的618活动执行建议，并说明优先级。",
            "role": "ops",
        }

        attachments_payload = []
        if attachment_id:
            attachments_payload.append({"id": attachment_id})
        if image_attachment_id:
            attachments_payload.append({"id": image_attachment_id})
        if attachments_payload:
            chat_payload["attachments"] = attachments_payload

        r_chat = runner.call("chat", "POST", "/api/chat", expected=(200,), json_body=chat_payload, timeout=120)
        conversation_id = str(_must_json(r_chat).get("conversation_id") or "")

        runner.call("chat_stream", "POST", "/api/chat/stream", expected=(200,), json_body={"message": "再给我3条渠道建议"}, timeout=120)
        if conversation_id:
            runner.call("chat_events", "GET", "/api/chat/events", expected=(200,), params={"conversation_id": conversation_id})
            runner.call("conversation_messages", "GET", f"/api/conversations/{conversation_id}/messages", expected=(200,))
            runner.call("conversation_export", "GET", f"/api/conversations/{conversation_id}/export", expected=(200,))

        runner.call("conversations", "GET", "/api/conversations", expected=(200,))
    finally:
        try:
            tmp_text.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            tmp_image.unlink(missing_ok=True)
        except Exception:
            pass

    # 4) 商品全链路
    r_prod = runner.call(
        "product_create",
        "POST",
        "/api/products",
        expected=(200,),
        json_body={
            "name": "全功能烟测商品",
            "category": "3C",
            "description": "用于全链路验证",
            "sku": f"SMOKE-{uuid.uuid4().hex[:6].upper()}",
            "selling_price": 199,
            "cost_price": 99,
            "supplier": "Smoke Supplier",
        },
    )
    product_id = int(_must_json(r_prod).get("id", 0))
    if product_id <= 0:
        print("[FAIL] product id missing")
        return 3

    runner.call("products_list", "GET", "/api/products", expected=(200,))
    runner.call("products_dashboard", "GET", "/api/products/dashboard", expected=(200,))
    runner.call("product_detail", "GET", f"/api/products/{product_id}", expected=(200,))
    runner.call("product_patch", "PATCH", f"/api/products/{product_id}", expected=(200,), json_body={"description": "烟测更新说明"})
    runner.call("product_set_pin", "POST", f"/api/products/{product_id}/set-pin", expected=(200,), json_body={"pin": "1234"})
    runner.call("product_reset_pin", "POST", f"/api/products/{product_id}/reset-pin", expected=(200,), json_body={})
    runner.call("product_transition", "POST", f"/api/products/{product_id}/transition", expected=(200,), json_body={"target_status": "选品中"})

    r_knowledge = runner.call(
        "product_knowledge_create",
        "POST",
        f"/api/products/{product_id}/knowledge",
        expected=(200,),
        json_body={"content": "核心卖点：轻便便携、低噪强风", "source_type": "manual", "content_type": "note", "confidence": 0.9},
    )
    knowledge_id = int(_must_json(r_knowledge).get("id", 0)) if _must_json(r_knowledge).get("id") else 0
    runner.call("product_knowledge_list", "GET", f"/api/products/{product_id}/knowledge", expected=(200,))
    if knowledge_id:
        runner.call("product_knowledge_patch", "PATCH", f"/api/products/{product_id}/knowledge/{knowledge_id}", expected=(200,), json_body={"content": "核心卖点更新：轻便+静音", "confidence": 0.86})

    r_material = runner.call(
        "product_material_create",
        "POST",
        f"/api/products/{product_id}/materials",
        expected=(200,),
        json_body={"material_type": "文案", "title": "烟测文案", "content": "夏季清凉风扇，轻便低噪。"},
    )
    material_id = int(_must_json(r_material).get("id", 0)) if _must_json(r_material).get("id") else 0
    runner.call("product_material_list", "GET", f"/api/products/{product_id}/materials", expected=(200,))
    if material_id:
        runner.call("product_material_patch", "PATCH", f"/api/products/{product_id}/materials/{material_id}", expected=(200,), json_body={"title": "烟测文案V2", "content": "升级版文案"})
        runner.call("product_material_versions", "GET", f"/api/products/{product_id}/materials/{material_id}/versions", expected=(200,))

    runner.call(
        "product_competitor_create",
        "POST",
        f"/api/products/{product_id}/competitors",
        expected=(200,),
        json_body={"name": "竞品A", "platform": "taobao", "price": 169.0, "rating": 4.6, "monthly_sales": "8000", "url": "https://example.com/item/1"},
    )
    runner.call("product_competitor_list", "GET", f"/api/products/{product_id}/competitors", expected=(200,))
    runner.call("product_competitor_changes", "GET", f"/api/products/{product_id}/competitors/changes", expected=(200,))
    runner.call("product_competitor_monitor", "POST", f"/api/products/{product_id}/competitors/monitor-now", expected=(200,))

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# 烟测资料\n\n用于产品文件上传验证。")
        tmp_doc = Path(f.name)
    try:
        with open(tmp_doc, "rb") as fd:
            r_file = runner.call(
                "product_file_upload",
                "POST",
                f"/api/products/{product_id}/files",
                expected=(200,),
                files={"file": (tmp_doc.name, fd, "text/markdown")},
                data={"description": "烟测文件"},
            )
        file_id = int(_must_json(r_file).get("id", 0)) if _must_json(r_file).get("id") else 0
        runner.call("product_file_list", "GET", f"/api/products/{product_id}/files", expected=(200,))
        if file_id:
            runner.call("product_file_patch", "PATCH", f"/api/products/{product_id}/files/{file_id}", expected=(200,), json_body={"description": "已更新描述"})
    finally:
        try:
            tmp_doc.unlink(missing_ok=True)
        except Exception:
            pass

    runner.call("product_assets_list", "GET", f"/api/products/{product_id}/assets", expected=(200,))
    runner.call("product_health", "GET", f"/api/products/{product_id}/health", expected=(200,))
    runner.call("product_manifest", "GET", f"/api/products/{product_id}/manifest", expected=(200,))
    runner.call("product_manifest_refresh", "POST", f"/api/products/{product_id}/manifest/refresh", expected=(200,))
    runner.call("product_suggestions_refresh", "POST", f"/api/products/{product_id}/refresh-suggestions", expected=(200,))
    runner.call("product_suggestions", "GET", f"/api/products/{product_id}/suggestions", expected=(200,))

    # 4.5) 本地素材目录解析闭环（P0*）
    with tempfile.TemporaryDirectory(prefix="smoke_local_materials_") as tmp_dir:
        local_root = Path(tmp_dir)
        (local_root / "docs").mkdir(parents=True, exist_ok=True)
        (local_root / "images").mkdir(parents=True, exist_ok=True)

        (local_root / "docs" / "guide.md").write_text(
            "# 本地素材导入\n\n用于验证目录级解析、去重与知识入库。",
            encoding="utf-8",
        )
        (local_root / "docs" / "faq.txt").write_text(
            "常见问题：如何提升点击率？答案：优化主图与标题一致性。",
            encoding="utf-8",
        )
        (local_root / "images" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\nSMOKELOCAL")

        r_local_scan = runner.call(
            "local_materials_scan",
            "POST",
            "/api/data-import/local-materials/scan",
            expected=(200,),
            json_body={
                "product_id": product_id,
                "folder_path": str(local_root),
                "recursive": True,
                "max_files": 200,
                "preview_limit": 50,
            },
        )
        scan_data = _must_json(r_local_scan)
        _record_check(
            runner,
            "local_materials_scan_count_check",
            "/api/data-import/local-materials/scan",
            int(scan_data.get("total_files", 0) or 0) >= 3,
            detail=f"total_files={int(scan_data.get('total_files', 0) or 0)} expected>=3",
        )
        _record_check(
            runner,
            "local_materials_scan_type_check",
            "/api/data-import/local-materials/scan",
            int((scan_data.get("by_type") or {}).get("docs", 0) or 0) >= 2
            and int((scan_data.get("by_type") or {}).get("images", 0) or 0) >= 1,
            detail=(
                f"by_type.docs={int((scan_data.get('by_type') or {}).get('docs', 0) or 0)}, "
                f"images={int((scan_data.get('by_type') or {}).get('images', 0) or 0)}"
            ),
        )

        r_local_import = runner.call(
            "local_materials_import",
            "POST",
            "/api/data-import/local-materials/import",
            expected=(200,),
            json_body={
                "product_id": product_id,
                "folder_path": str(local_root),
                "recursive": True,
                "max_files": 200,
                "ingest_text": True,
                "refresh_manifest": True,
                "max_knowledge_chars": 4000,
                "max_file_size_bytes": 10 * 1024 * 1024,
            },
        )
        import_data = _must_json(r_local_import)
        run_id = int(import_data.get("run_id", 0) or 0)
        _record_check(
            runner,
            "local_materials_import_runid_check",
            "/api/data-import/local-materials/import",
            run_id > 0,
            detail=f"run_id={run_id} expected>0",
        )
        _record_check(
            runner,
            "local_materials_import_outcome_check",
            "/api/data-import/local-materials/import",
            bool(import_data.get("ok") is True)
            and int(import_data.get("imported_assets", 0) or 0) >= 3
            and int(import_data.get("imported_knowledge", 0) or 0) >= 2,
            detail=(
                f"ok={import_data.get('ok')}, imported_assets={int(import_data.get('imported_assets', 0) or 0)}, "
                f"imported_knowledge={int(import_data.get('imported_knowledge', 0) or 0)}"
            ),
        )

        r_local_runs = runner.call(
            "local_materials_runs",
            "GET",
            "/api/data-import/local-materials/runs",
            expected=(200,),
            params={"product_id": product_id, "limit": 20},
        )
        runs_data = _must_json(r_local_runs)
        run_ids = {
            int((row or {}).get("id", 0) or 0)
            for row in (runs_data.get("runs") or [])
            if isinstance(row, dict)
        }
        _record_check(
            runner,
            "local_materials_runs_include_check",
            "/api/data-import/local-materials/runs",
            run_id > 0 and run_id in run_ids,
            detail=f"run_id={run_id}, returned_ids_count={len(run_ids)}",
        )

        if run_id > 0:
            r_local_run_detail = runner.call(
                "local_materials_run_detail",
                "GET",
                f"/api/data-import/local-materials/runs/{run_id}",
                expected=(200,),
                params={"include_items": "true", "items_limit": 500},
            )
            detail_data = _must_json(r_local_run_detail)
            run_obj = detail_data.get("run") or {}
            _record_check(
                runner,
                "local_materials_run_detail_consistency_check",
                f"/api/data-import/local-materials/runs/{run_id}",
                int(run_obj.get("product_id", 0) or 0) == product_id
                and int(run_obj.get("total_files", 0) or 0) >= 3
                and int(detail_data.get("item_count", 0) or 0) >= 3,
                detail=(
                    f"run.product_id={int(run_obj.get('product_id', 0) or 0)}, "
                    f"run.total_files={int(run_obj.get('total_files', 0) or 0)}, "
                    f"item_count={int(detail_data.get('item_count', 0) or 0)}"
                ),
            )

        # 重复导入用于验证去重逻辑（应返回200并产生skipped_files）
        r_local_reimport = runner.call(
            "local_materials_reimport_dedupe",
            "POST",
            "/api/data-import/local-materials/import",
            expected=(200,),
            json_body={
                "product_id": product_id,
                "folder_path": str(local_root),
                "recursive": True,
                "max_files": 200,
                "ingest_text": True,
            },
        )
        reimport_data = _must_json(r_local_reimport)
        _record_check(
            runner,
            "local_materials_reimport_dedupe_check",
            "/api/data-import/local-materials/import",
            int(reimport_data.get("imported_assets", 0) or 0) == 0
            and int(reimport_data.get("skipped_files", 0) or 0) >= 3,
            detail=(
                f"imported_assets={int(reimport_data.get('imported_assets', 0) or 0)}, "
                f"skipped_files={int(reimport_data.get('skipped_files', 0) or 0)}"
            ),
        )

    # 5) 工作区全链路
    r_ws = runner.call("workspace_create", "POST", "/api/workspaces", expected=(200,), json_body={"title": "全功能烟测工作区", "workspace_type": "general", "product_id": product_id})
    ws_id = int(_must_json(r_ws).get("id", 0))
    if ws_id <= 0:
        print("[FAIL] workspace id missing")
        return 4

    runner.call("workspaces_list", "GET", "/api/workspaces", expected=(200,))
    runner.call("workspace_detail", "GET", f"/api/workspaces/{ws_id}", expected=(200,))
    runner.call("workspace_patch", "PATCH", f"/api/workspaces/{ws_id}", expected=(200,), json_body={"title": "全功能烟测工作区-更新"})

    runner.call("workspace_memory_save", "POST", f"/api/workspaces/{ws_id}/memory", expected=(200,), json_body={"role": "ops", "key": "promo_plan", "content": "618执行策略v1", "memory_type": "fact"})
    runner.call("workspace_memory_get", "GET", f"/api/workspaces/{ws_id}/memory", expected=(200,))

    runner.call("workspace_shared_context_save", "POST", f"/api/workspaces/{ws_id}/shared-context", expected=(200,), json_body={"content": "跨角色同步：主推爆款与库存策略"})
    runner.call("workspace_shared_context_get", "GET", f"/api/workspaces/{ws_id}/shared-context", expected=(200,))

    r_wstask = runner.call("workspace_task_create", "POST", f"/api/workspaces/{ws_id}/tasks", expected=(200,), json_body={"title": "执行素材投放", "owner_role": "ops", "description": "完成3条投放素材"})
    task_id = int(_must_json(r_wstask).get("id", 0)) if _must_json(r_wstask).get("id") else 0
    runner.call("workspace_task_list", "GET", f"/api/workspaces/{ws_id}/tasks", expected=(200,))
    if task_id:
        runner.call("workspace_task_execute", "POST", f"/api/workspaces/{ws_id}/tasks/{task_id}/execute", expected=(200,))
        runner.call("workspace_task_patch", "PATCH", f"/api/workspaces/{ws_id}/tasks/{task_id}", expected=(200,), json_body={"priority": 2})

    runner.call("workspace_chat", "POST", f"/api/workspaces/{ws_id}/chat", expected=(200,), json_body={"message": "请基于工作区上下文给执行清单。"}, timeout=240)
    runner.call("workspace_chat_stream", "POST", f"/api/workspaces/{ws_id}/chat/stream", expected=(200,), json_body={"message": "再给一个复盘模板"}, timeout=120)
    runner.call("workspace_conversations", "GET", f"/api/workspaces/{ws_id}/conversations", expected=(200,))

    autoflow_payload = {"goal": "完成618预热投放计划", "requirements": ["输出执行步骤", "标注负责人", "给出时间节点"], "max_steps": 1, "response_timeout_seconds": 60}
    runner.call("workspace_autoflow_plan", "POST", f"/api/workspaces/{ws_id}/autoflow/plan", expected=(200,), json_body=autoflow_payload)
    r_autorun = runner.call("workspace_autoflow_plan_and_run", "POST", f"/api/workspaces/{ws_id}/autoflow/plan-and-run", expected=(200,), json_body=autoflow_payload, timeout=240)
    run_id = int(_must_json(r_autorun).get("run", {}).get("id", 0) or _must_json(r_autorun).get("id", 0) or 0)
    runner.call("workspace_autoflow_runs", "GET", f"/api/workspaces/{ws_id}/autoflow/runs", expected=(200,))
    if run_id:
        runner.call("workspace_autoflow_actions", "GET", f"/api/workspaces/{ws_id}/autoflow/runs/{run_id}/actions", expected=(200,))
        runner.call("workspace_autoflow_report", "GET", f"/api/workspaces/{ws_id}/autoflow/runs/{run_id}/report", expected=(200,))

    # 6) 活动/情报/简报
    r_campaign = runner.call("campaign_create", "POST", "/api/campaigns", expected=(200,), json_body={"name": "烟测活动", "status": "draft", "product_id": product_id})
    campaign_id = int(_must_json(r_campaign).get("id", 0) or 0)
    runner.call("campaign_list", "GET", "/api/campaigns", expected=(200,))
    if campaign_id:
        runner.call("campaign_detail", "GET", f"/api/campaigns/{campaign_id}", expected=(200,))
        runner.call("campaign_patch", "PATCH", f"/api/campaigns/{campaign_id}", expected=(200,), json_body={"status": "active"})

    runner.call("intelligence_dashboard", "GET", "/api/intelligence/dashboard", expected=(200,))
    runner.call("intelligence_episodes", "GET", "/api/intelligence/episodes", expected=(200,))
    runner.call("intelligence_knowledge", "GET", "/api/intelligence/knowledge", expected=(200,))
    runner.call("intelligence_skill_stats", "GET", "/api/intelligence/skill-stats", expected=(200,))
    runner.call("intelligence_store_hub", "GET", "/api/intelligence/store-hub", expected=(200,))
    runner.call("intelligence_user_model", "GET", "/api/intelligence/user-model", expected=(200,))

    runner.call("autopilot_schedule_get", "GET", "/api/intelligence/store-hub/autopilot-schedule", expected=(200,))
    runner.call("autopilot_schedule_put", "PUT", "/api/intelligence/store-hub/autopilot-schedule", expected=(200,), json_body={"enabled": True, "schedule_type": "daily", "cron_expr": "0 9 * * *"})
    runner.call("autopilot_run_due", "POST", "/api/intelligence/store-hub/autopilot-schedule/run-due", expected=(200,), json_body={}, timeout=240)
    runner.call("autopilot_runs", "GET", "/api/intelligence/store-hub/autopilot-runs", expected=(200,))
    runner.call("daily_report", "GET", "/api/intelligence/store-hub/daily-report", expected=(200,))

    runner.call("daily_briefing", "GET", "/api/daily-briefing", expected=(200,))
    runner.call("notifications", "GET", "/api/notifications", expected=(200,))
    runner.call("alerts", "GET", "/api/alerts", expected=(200,))
    runner.call("alerts_summary", "GET", "/api/alerts/summary", expected=(200,))
    runner.call("alerts_dismiss_all", "POST", "/api/alerts/dismiss-all", expected=(200,), json_body={})

    # 7) 数据导入 / 平台连接 / 执行流水线
    runner.call("data_import_quick_entry", "POST", "/api/data-import/quick-entry", expected=(200,), json_body={"platform": "general", "date": "2026-04-04", "gmv": 12888, "uv": 3200, "orders": 123, "conversion_rate": 3.2})
    runner.call("data_import_summary", "GET", "/api/data-import/summary", expected=(200,), params={"days": 30})
    runner.call("data_import_metrics", "GET", "/api/data-import/metrics", expected=(200,), params={"start_date": "2026-04-01", "end_date": "2026-04-30"})
    runner.call("data_import_template", "GET", "/api/data-import/template/taobao", expected=(200,))

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write("date,platform,uv,pv,gmv,orders,conversion_rate\n2026-04-03,taobao,1000,2500,5888.5,48,0.048\n")
        tmp_csv = Path(f.name)
    try:
        with open(tmp_csv, "rb") as fd:
            runner.call(
                "data_import_upload",
                "POST",
                "/api/data-import/upload",
                expected=(200,),
                files={"file": (tmp_csv.name, fd, "text/csv")},
                data={"platform": "taobao"},
            )
    finally:
        try:
            tmp_csv.unlink(missing_ok=True)
        except Exception:
            pass

    runner.call("platforms", "GET", "/api/platform-connect/platforms", expected=(200,))
    runner.call("platform_connections", "GET", "/api/platform-connect/connections", expected=(200,))
    runner.call("platform_connect", "POST", "/api/platform-connect/connect", expected=(200,), json_body={"platform": "taobao", "credentials": {"app_key": "demo", "app_secret": "demo", "session_key": "demo"}})
    runner.call("platform_test", "POST", "/api/platform-connect/test/taobao", expected=(200,))
    runner.call("platform_sync_status", "GET", "/api/platform-connect/sync-status", expected=(200,))
    runner.call("platform_summary", "GET", "/api/platform-connect/summary", expected=(200,))
    runner.call("platform_sync_now", "POST", "/api/platform-connect/sync-now", expected=(200,), json_body={})

    runner.call("execution_suggest", "POST", "/api/execution/suggest", expected=(200,), json_body={"action_type": "update_price", "description": "烟测改价", "platform": "taobao", "payload": {"product_id": str(product_id), "new_price": 188}, "expected_impact": "提升转化"})
    r_pending = runner.call("execution_pending", "GET", "/api/execution/pending", expected=(200,))
    pending_items = _must_json(r_pending).get("items") or []
    if pending_items:
        eid = int(pending_items[0].get("id", 0))
        if eid:
            runner.call("execution_reject", "POST", f"/api/execution/{eid}/reject", expected=(200,), json_body={"reason": "smoke_reject"})

    r_suggest2 = runner.call("execution_suggest2", "POST", "/api/execution/suggest", expected=(200,), json_body={"action_type": "update_inventory", "description": "烟测改库存", "platform": "taobao", "payload": {"product_id": str(product_id), "quantity": 66}, "expected_impact": "避免缺货"})
    eid2 = int(_must_json(r_suggest2).get("id", 0) or 0)
    if eid2:
        runner.call("execution_approve", "POST", f"/api/execution/{eid2}/approve", expected=(200,), json_body={})

    # 8) Kernel / Long Tasks / Admin
    runner.call("kernel_modules", "GET", "/api/kernel/modules", expected=(200,))
    runner.call("kernel_workspace", "GET", "/api/kernel/workspace", expected=(200,))
    runner.call("kernel_runtime_roles", "GET", "/api/kernel/runtime-roles", expected=(200,))
    runner.call("kernel_runtime_profile", "POST", "/api/kernel/runtime-profile", expected=(200,), json_body={"message": "帮我规划一个平台投放执行方案", "runtime_roles": ["ops", "data"], "max_roles": 4})
    runner.call("kernel_generalization_status", "GET", "/api/kernel/generalization-status", expected=(200,))
    runner.call("kernel_generalization_fixes", "GET", "/api/kernel/generalization-fixes", expected=(200,))
    runner.call("kernel_execute", "POST", "/api/kernel/execute", expected=(200,), json_body={"message": "给一个简短执行建议", "role": "ops"}, timeout=120)

    r_task = runner.call("long_task_submit", "POST", "/api/tasks/", expected=(200,), json_body={"task_type": "weekly_report", "title": "烟测周报任务", "params": {"days": 7}})
    long_task_id = int(_must_json(r_task).get("task_id", 0) or 0)
    runner.call("long_task_list", "GET", "/api/tasks/", expected=(200,))
    if long_task_id:
        runner.call("long_task_get", "GET", f"/api/tasks/{long_task_id}", expected=(200,))
        _wait_task_terminal(runner, long_task_id, timeout_seconds=120)
        runner.call("long_task_stream", "GET", f"/api/tasks/{long_task_id}/stream", expected=(200,), timeout=120)

    runner.call("admin_metrics", "GET", "/api/admin/metrics", expected=(200,))
    runner.call("admin_metrics_dashboard", "GET", "/api/admin/metrics/dashboard", expected=(200,))
    runner.call("admin_trust", "GET", "/api/admin/trust", expected=(200,))
    runner.call("admin_alerts", "GET", "/api/admin/alerts", expected=(200,))
    runner.call("admin_learnings", "GET", "/api/admin/learnings", expected=(200,))
    runner.call("admin_features", "GET", "/api/admin/system/features", expected=(200,))
    runner.call("admin_system_health", "GET", "/api/admin/system/health", expected=(200,))
    runner.call("admin_skills", "GET", "/api/admin/skills", expected=(200,))

    # 9) 回收站链路 + 清理
    runner.call("product_delete_soft", "DELETE", f"/api/products/{product_id}", expected=(200,), json_body={"pin": "1234"})
    r_trash = runner.call("trash_list", "GET", "/api/trash", expected=(200,))
    runner.call("trash_summary", "GET", "/api/trash/summary", expected=(200,))
    items = _must_json(r_trash).get("items") or []
    restore_item = None
    for item in items:
        if str(item.get("original_table", "")) == "products" and int(item.get("original_id", 0) or 0) == product_id:
            restore_item = item
            break
    if not restore_item and items:
        restore_item = items[0]
    if restore_item:
        trash_id = int(restore_item.get("id", 0) or 0)
        if trash_id:
            expected_restore = (200,) if int(restore_item.get("original_id", 0) or 0) == product_id else (200, 409)
            runner.call("trash_restore", "POST", f"/api/trash/{trash_id}/restore", expected=expected_restore)

    runner.call("platform_delete", "DELETE", "/api/platform-connect/taobao", expected=(200,))

    # 汇总输出
    summary = runner.summary()
    report_dir = ROOT / "tools" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"full_feature_smoke_{int(time.time())}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 88)
    print(f"Smoke report: {report_path}")
    print(f"Total={summary['total']} Passed={summary['passed']} Failed={summary['failed']}")

    if summary["failed"]:
        print("\nFailed steps:")
        for row in summary["results"]:
            if not row["ok"]:
                print(f"- {row['method']} {row['path']} status={row['status']} detail={row.get('detail','')}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

