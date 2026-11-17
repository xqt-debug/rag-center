"""验收图片 OCR、原图预览和粘贴文本；只创建并清理专用验收知识库。"""

import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8000"


def main():
    config = dotenv_values(ROOT / ".env")
    if not config.get("RAG_SMOKE_USERNAME") or not config.get("RAG_SMOKE_PASSWORD"):
        raise SystemExit("请先注册测试账号，并配置 RAG_SMOKE_USERNAME / RAG_SMOKE_PASSWORD")
    with httpx.Client(trust_env=False) as client:
        health = client.get(ORIGIN + "/api/health").json()
    if health["embedding_provider"] != "local-test" and not health["embedding_configured"]:
        raise SystemExit("此验收需要已配置的向量服务，或显式启用测试向量的独立环境。")
    label = "测试索引（非模型）" if health["embedding_provider"] == "local-test" else "索引已完成"
    artifacts = ROOT / "data" / "screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    fixture = ROOT / "tests" / "fixtures" / "ocr-note.png"
    gif = artifacts / "ocr-animation.gif"
    with Image.open(fixture) as original:
        second = original.convert("RGB")
        second.putpixel((0, 0), (0, 0, 0))
        original.save(gif, save_all=True, append_images=[second], duration=1000, loop=0)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        base, csrf = None, None
        try:
            page.goto(ORIGIN)
            page.get_by_placeholder("输入账号").fill(config.get("RAG_SMOKE_USERNAME") or "")
            page.get_by_placeholder("输入密码").fill(config.get("RAG_SMOKE_PASSWORD") or "")
            with page.expect_response("**/api/auth/login") as response:
                page.get_by_role("button", name="登录知识空间").click()
            csrf = response.value.json()["csrf"]
            page.get_by_role("button", name="新建知识库", exact=True).click()
            page.get_by_placeholder("例如：项目资料、学习笔记").fill("图片文本验收-" + uuid.uuid4().hex[:8])
            with page.expect_response("**/api/actions/create_knowledge_base") as response:
                page.get_by_role("button", name="保存知识库").click()
            base = response.value.json()
            page.get_by_role("button", name="粘贴文本", exact=True).click()
            page.get_by_label("文件名称（.txt 或 .md）").fill("纯文本验收.txt")
            page.get_by_label("文本内容", exact=True).fill("纯文本学习资料：TCP 通过重传保证可靠传输。")
            page.get_by_role("button", name="保存并建立索引").click()
            text_row = page.get_by_role("row").filter(has_text="纯文本验收.txt")
            expect(text_row.get_by_text(label, exact=True)).to_be_visible(timeout=90000)
            page.locator('input[type="file"][multiple]').set_input_files([str(fixture), str(gif)])
            for name in (fixture.name, gif.name):
                row = page.get_by_role("row").filter(has_text=name)
                expect(row.get_by_text(label, exact=True)).to_be_visible(timeout=90000)
                row.get_by_role("button", name="预览文件", exact=True).click()
                image = page.locator(".image-preview img")
                expect(image).to_be_visible()
                image.evaluate("img => img.decode()")
                expect(page.locator(".document-preview")).to_contain_text("TCP")
                if name.endswith(".gif"):
                    source = image.get_attribute("src")
                    data = page.request.get(ORIGIN + source)
                    assert data.body() == gif.read_bytes()
                page.screenshot(
                    path=str(artifacts / ("gif-preview.png" if name.endswith(".gif") else "ocr-preview.png")),
                    full_page=True,
                )
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="搜索实验室", exact=True).click()
            page.get_by_placeholder("你想从资料中找到什么？").fill("TCP")
            page.get_by_role("button", name="关键词检索", exact=True).click()
            page.get_by_role("button", name="搜索", exact=True).click()
            expect(page.locator(".result-text").first).to_contain_text("TCP")
            assert not errors, errors
            print(
                "Media UI smoke passed: pasted text, PNG/GIF OCR, preview, original GIF bytes, responsive layout, keyword search."
            )
        finally:
            if base and csrf:
                deleted = page.request.post(
                    ORIGIN + "/api/actions/delete_knowledge_base",
                    headers={"X-CSRF-Token": csrf},
                    data={"knowledge_base_id": base["id"], "expected_revision": base["revision"]},
                )
                assert deleted.ok, "专用验收知识库清理失败"
            browser.close()


if __name__ == "__main__":
    main()
