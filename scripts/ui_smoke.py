"""使用本机 Edge 验证真实网站流程，仅创建和清理专用验收资料。"""

import json
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "data" / "screenshots"


def main():
    config = dotenv_values(ROOT / ".env")
    health = httpx.get("http://localhost:8000/api/health", timeout=10).json()
    testing = health["embedding_provider"] == "local-test"
    if not testing and not health["embedding_configured"]:
        raise SystemExit("尚未配置向量模型，无法验收索引完成流程；请先配置并重启服务。")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    fixture = ARTIFACTS / "验收资料.md"
    fixture.write_text(
        "# 接入验收资料\n\n本文件仅用于自动化验收，测试后删除。\n\n"
        "## 文件处理\n资料上传后异步完成解析、分块和索引。\n\n"
        "## 检索验证\n验收编号 QA-2026-113，关键词检索应找到本段。\n",
        encoding="utf-8",
    )
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto("http://localhost:8000", wait_until="networkidle")
        expect(page.get_by_role("heading", name="欢迎来到 RAG Center")).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / "login-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(ARTIFACTS / "login-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_placeholder("输入测试账号").fill(
            os.environ.get("RAG_DEV_USERNAME") or config.get("RAG_DEV_USERNAME") or "admin"
        )
        page.get_by_placeholder("输入测试密码").fill(
            os.environ.get("RAG_DEV_PASSWORD") or config.get("RAG_DEV_PASSWORD") or "admin123"
        )
        page.get_by_role("button", name="登录，进入知识空间").click()
        expect(page.get_by_role("heading", name="我的知识库")).to_be_visible()
        page.get_by_role("button", name="新建知识库", exact=True).click()
        page.get_by_placeholder("例如：项目资料、学习笔记").fill("自动化验收空间")
        page.get_by_placeholder("这个知识库里放些什么？").fill("仅用于验证真实页面流程，验收结束后自动删除。")
        page.get_by_role("button", name="保存知识库").click()
        expect(page.get_by_role("heading", name="自动化验收空间 / 文件")).to_be_visible()
        page.locator('input[type="file"][multiple]').set_input_files(str(fixture))
        label = "测试索引（非模型）" if testing else "索引已完成"
        expect(page.get_by_text(label, exact=True)).to_be_visible(timeout=90000)
        page.screenshot(path=str(ARTIFACTS / "library-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(ARTIFACTS / "library-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="预览文件", exact=True).click()
        expect(page.locator(".document-preview")).to_contain_text("QA-2026-113")
        page.get_by_role("button", name="关闭", exact=True).click()
        page.get_by_role("button", name="搜索实验室", exact=True).click()
        page.get_by_placeholder("你想从资料中找到什么？").fill("QA-2026-113")
        page.get_by_role("button", name="关键词检索", exact=True).click()
        page.get_by_role("button", name="搜索", exact=True).click()
        expect(page.locator(".result-text")).to_contain_text("QA-2026-113")
        page.screenshot(path=str(ARTIFACTS / "search-desktop.png"), full_page=True)
        page.get_by_role("button", name="MCP 接入", exact=False).first.click()
        page.get_by_role("button", name="创建密钥", exact=True).click()
        page.get_by_placeholder("例如：我的桌面 Agent").fill("自动化验收密钥")
        page.get_by_label("权限模板").select_option("full")
        page.get_by_role("button", name="生成密钥与配置").click()
        expect(page.locator(".config-code")).to_be_visible()
        config = json.loads(page.locator(".config-code").inner_text())
        assert config["mcpServers"]["rag-center"]["headers"]["Authorization"].startswith("Bearer rag_")
        page.get_by_role("button", name="已保存，完成").click()
        page.get_by_role("button", name="撤销", exact=True).last.click()
        page.get_by_role("button", name="确认撤销").click()
        expect(page.get_by_text("已撤销", exact=True).last).to_be_visible()
        page.get_by_role("button", name="知识库", exact=True).click()
        page.get_by_role("button", name="删除知识库", exact=True).click()
        page.get_by_role("button", name="确认删除").click()
        expect(page.get_by_role("heading", name="自动化验收空间 / 文件")).not_to_be_visible()
        assert not errors, errors
        browser.close()
    print(
        "Browser smoke passed: login, responsive layout, upload, preview, keyword retrieval, key/config, revoke, delete."
    )
    print(f"Screenshots: {ARTIFACTS}")


if __name__ == "__main__":
    main()
