import os
import asyncio
import json
import urllib.request
from playwright.async_api import async_playwright

TARGET_URL = "https://bot-hosting.net/panel/earn"

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()
RAW_PROXIES = os.environ.get("PROXY_SERVER", "").strip()
CAPTCHAKINGS_API_KEY = os.environ.get("CAPTCHAKINGS_API_KEY", "").strip()

KNOWN_HCAPTCHA_SITEKEY = "21335a07-5b97-4a79-b1e9-b197dc35017a"
MAX_LOOPS = 40

def get_proxy_list():
    if not RAW_PROXIES:
        return []
    proxies = RAW_PROXIES.replace('\n', ',').split(',')
    return [p.strip() for p in proxies if p.strip()]

# ====================== CaptchaKings 打码（2026 最稳） ======================
async def solve_hcaptcha_captchakings(page_url: str, sitekey: str, api_key: str, proxy: str = None):
    print(f"[CaptchaKings] 提交 hCaptcha 任务 → {page_url}")
    
    create_url = "https://api.captchakings.com/createTask"
    task = {
        "type": "HCaptchaTaskProxyless",
        "websiteURL": page_url,
        "websiteKey": sitekey
    }
    if proxy:
        task["proxy"] = proxy
    
    payload = {"clientKey": api_key, "task": task}
    
    try:
        req = urllib.request.Request(create_url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=15)
        data = json.loads(resp.read().decode())
        
        if data.get("errorId") != 0:
            raise Exception(data.get("errorDescription", str(data)))
        
        task_id = data["taskId"]
        print(f"[CaptchaKings] 任务创建成功 TaskID: {task_id}")
    except Exception as e:
        print(f"[CaptchaKings] 创建任务失败: {e}")
        raise
    
    print("[CaptchaKings] 等待解决中（通常 8-25 秒）...")
    result_url = "https://api.captchakings.com/getTaskResult"
    result_payload = {"clientKey": api_key, "taskId": task_id}
    
    for _ in range(40):
        await asyncio.sleep(5)
        try:
            req = urllib.request.Request(result_url, data=json.dumps(result_payload).encode(), headers={'Content-Type': 'application/json'})
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
            data = json.loads(resp.read().decode())
            
            if data.get("errorId") != 0:
                raise Exception(data.get("errorDescription"))
            
            if data.get("status") == "ready":
                token = data["solution"].get("gRecaptchaResponse") or data["solution"].get("token")
                print(f"[CaptchaKings] ✅ Token 获取成功！长度 {len(token)}")
                return token
        except:
            pass
    
    raise Exception("CaptchaKings 轮询超时")

# ====================== 其他辅助函数 ======================
async def get_working_proxy(p, proxy_list):
    print(f"[Proxy] 测试 {len(proxy_list)} 个代理...")
    for proxy in proxy_list:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True, proxy={"server": proxy})
            context = await browser.new_context()
            page = await context.new_page()
            response = await page.goto("https://bot-hosting.net/", timeout=20000, wait_until="domcontentloaded")
            if response and response.status == 200:
                print(f"[Proxy] ✅ 可用: {proxy}")
                await browser.close()
                return proxy
        except Exception as e:
            print(f"[Proxy] ❌ {proxy}: {e}")
        finally:
            if browser:
                try: await browser.close()
                except: pass
    return None

async def safe_screenshot(page, filename):
    try:
        await page.screenshot(path=filename, timeout=8000)
        print(f"[Debug] 保存截图: {filename}")
    except:
        pass

async def inject_token(page, token: str, is_turnstile: bool = False):
    await page.evaluate(f'''
        const token = "{token}";
        const isTurnstile = {str(is_turnstile).lower()};
        const name = isTurnstile ? "cf-turnstile-response" : "h-captcha-response";
        
        let ta = document.querySelector(`textarea[name="${{name}}"]`);
        if (!ta) {{
            ta = document.createElement("textarea");
            ta.name = name;
            ta.style.display = "none";
            document.body.appendChild(ta);
        }}
        ta.value = token;
        
        if (isTurnstile) {{
            window.turnstile = {{ getResponse: () => token }};
        }} else {{
            window.hcaptcha = {{ getResponse: () => token, execute: () => Promise.resolve(token) }};
        }}
        
        document.querySelectorAll(".btn-success, button:has-text('Claim'), button:has-text('Click here')").forEach(btn => {{
            btn.removeAttribute("disabled");
            btn.classList.remove("disabled");
            btn.click();
        }});
        console.log("[Inject] Token 已注入并触发点击");
    ''')

async def main():
    if not AUTH_TOKEN or not CAPTCHAKINGS_API_KEY:
        print("❌ 缺少 AUTH_TOKEN 或 CAPTCHAKINGS_API_KEY")
        return

    proxy_list = get_proxy_list()
    working_proxy = None
    if proxy_list:
        async with async_playwright() as p:
            working_proxy = await get_working_proxy(p, proxy_list)

    async with async_playwright() as p:
        launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-setuid-sandbox"]}
        if working_proxy:
            launch_args["proxy"] = {"server": working_proxy}

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page = await context.new_page()
        await page.goto("https://bot-hosting.net/", wait_until="domcontentloaded", timeout=60000)
        await page.evaluate(f"window.localStorage.setItem('token', '{AUTH_TOKEN}');")
        print("[Auth] Token 注入完成")

        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)

        loop_count = 0
        while loop_count < MAX_LOOPS:
            loop_count += 1
            print(f"\n=== 第 {loop_count}/{MAX_LOOPS} 次循环 ===")
            await asyncio.sleep(3)

            # 关闭弹窗
            try: await page.locator("button:has-text('X'), .close").first.click(timeout=3000)
            except: pass

            # 检查冷却状态
            try:
                btn_text = await page.locator(".btn-success").first.inner_text(timeout=5000)
                if any(x in btn_text.lower() for x in ["cooldown", "cool down", "wait"]):
                    print("🎉 当日配额已满，正常退出")
                    await safe_screenshot(page, f"success_cooldown_{loop_count}.png")
                    break
            except: pass

            # 【已修复】验证码检测 - 拆分成独立判断
            has_captcha = (
                await page.locator("iframe[src*='hcaptcha.com']").count() > 0 or
                await page.locator(".cf-turnstile").count() > 0 or
                await page.locator("text=Complete the captcha").count() > 0
            )

            if has_captcha:
                print("[Captcha] 检测到验证码 → 启动 CaptchaKings")
                try:
                    sitekey = await page.evaluate('''() => document.querySelector("[data-sitekey]")?.getAttribute("data-sitekey") || null''') or KNOWN_HCAPTCHA_SITEKEY
                    is_turnstile = await page.locator(".cf-turnstile").count() > 0
                    
                    token = await solve_hcaptcha_captchakings(page.url, sitekey, CAPTCHAKINGS_API_KEY, working_proxy)
                    await inject_token(page, token, is_turnstile)
                    await asyncio.sleep(4)
                except Exception as e:
                    print(f"[Captcha] 打码失败: {e}")
                    await safe_screenshot(page, f"captcha_fail_{loop_count}.png")
                    break

            # 点击 Claim
            try:
                await page.locator(".btn-success, button:has-text('Click here to claim')").first.click(timeout=8000, force=True)
                print("[Action] 已点击 Claim")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"[Action] 点击失败: {e}")
                await safe_screenshot(page, f"click_fail_{loop_count}.png")
                break

            # 等待 OK
            try:
                await page.wait_for_selector("button:has-text('OK')", timeout=25000)
                await page.locator("button:has-text('OK')").first.click(timeout=5000)
                print(f"✅ 第 {loop_count} 次收集成功")
            except:
                print(f"[Warning] 未找到 OK 按钮")

            await asyncio.sleep(5)

        print(f"[结束] 执行 {loop_count} 次循环，关闭浏览器")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
