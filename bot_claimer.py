import os
import asyncio
import re
from playwright.async_api import async_playwright

# 导入 2Captcha 官方库
try:
    from twocaptcha import TwoCaptcha
except ImportError:
    TwoCaptcha = None

TARGET_URL = "https://bot-hosting.net/panel/earn"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
RAW_PROXIES = os.environ.get("PROXY_SERVER", "")

# 获取 2Captcha API Key 并初始化
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")
solver = TwoCaptcha(TWOCAPTCHA_API_KEY) if TWOCAPTCHA_API_KEY and TwoCaptcha else None

# 我们从上一轮日志中成功提取到的目标网站固定 Sitekey
KNOWN_SITEKEY = "21335a07-5b97-4a79-b1e9-b197dc35017a"

def get_proxy_list():
    if not RAW_PROXIES:
        return []
    proxies = RAW_PROXIES.replace('\n', ',').split(',')
    return [p.strip() for p in proxies if p.strip()]

async def get_working_proxy(p, proxy_list):
    print(f"[状态] 发现 {len(proxy_list)} 个备选代理，开始快速可用性检测...")
    for proxy in proxy_list:
        print(f"[检测] 正在测试代理: {proxy}")
        try:
            browser = await p.chromium.launch(headless=True, proxy={"server": proxy})
            context = await browser.new_context()
            page = await context.new_page()
            
            response = await page.goto("https://bot-hosting.net/", timeout=15000, wait_until="commit")
            
            if response and response.status == 200:
                print(f"[成功] 代理连通性良好: {proxy}")
                await browser.close()
                return proxy
            else:
                print(f"[警告] 代理连通，但返回状态码异常: {response.status if response else 'None'}")
                await browser.close()
        except Exception as e:
            print(f"[失败] 代理超时或无法连接: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            
    print("[致命错误] 代理池中所有代理均检测失败！")
    return None

async def safe_screenshot(page, path):
    try:
        await page.screenshot(path=path, timeout=5000)
    except Exception as e:
        print(f"[警告] 截图保存超时或失败 ({path})，跳过截图。")

async def safe_dump_html(page, path):
    try:
        html_content = await page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"[状态] 已成功保存当前页面 HTML 到 {path}")
    except Exception as e:
        pass

async def inject_token_and_login(context):
    page = await context.new_page()
    
    stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.navigator.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """
    await context.add_init_script(stealth_js)
    
    print("[状态] 正在初始化登录状态并注入底层伪装防护...")
    try:
        await page.goto("https://bot-hosting.net/", wait_until="domcontentloaded", timeout=60000) 
        await page.evaluate(f"window.localStorage.setItem('token', '{AUTH_TOKEN}');")
        print("[状态] Token 注入完成。")
    except Exception as e:
        print(f"[错误] 注入 Token 时访问主页失败: {e}")
        await safe_screenshot(page, "debug_00_inject_token_error.png")
    return page

async def main():
    if not AUTH_TOKEN:
        print("[错误] 未找到 AUTH_TOKEN 环境变量，脚本终止。")
        return

    proxy_list = get_proxy_list()

    async with async_playwright() as p:
        working_proxy = None
        if proxy_list:
            working_proxy = await get_working_proxy(p, proxy_list)
            if not working_proxy:
                print("[中止] 没有可用代理，放弃本次任务。")
                return

        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        }
        
        if working_proxy:
            print(f"[状态] 主流程将使用验证通过的代理: {working_proxy}")
            launch_args["proxy"] = {"server": working_proxy}
        elif not proxy_list:
             print(f"[状态] 未配置代理，将使用直连网络运行。")

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        page = await inject_token_and_login(context)
        
        print(f"[状态] 正在跳转至目标收集页面: {TARGET_URL}")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(8) 
        except Exception as e:
            print(f"[致命错误] 访问收集页面超时: {e}")
            await safe_screenshot(page, "debug_01_timeout_error.png")
            await safe_dump_html(page, "debug_01_timeout_error.html")
            await browser.close()
            return
            
        await safe_screenshot(page, "debug_01_after_login.png")

        for i in range(1, 11):
            print(f"\n--- [流程] 开始第 {i}/10 次收集循环 ---")

            cooldown_count = await page.locator("text=You are on cooldown!").count()
            if cooldown_count > 0:
                print("[中止] 检测到处于冷却时间，今日任务可能已完成。")
                await safe_screenshot(page, f"debug_cooldown_loop_{i}.png")
                break

            await asyncio.sleep(4)
            
            # 判断是否需要打码：只要存在 "Complete the captcha" 文字，就需要打码（不论 iframe 是否因代理被拦截加载）
            needs_captcha = await page.locator("text='Complete the captcha'").count() > 0 or await page.locator("iframe[src*='hcaptcha.com']").count() > 0

            if needs_captcha:
                print("[动作] 确认页面需要处理 hCaptcha (触发 2Captcha API 流程)...")
                await safe_screenshot(page, f"debug_hcaptcha_before_loop_{i}.png")
                
                try:
                    if not solver:
                        print("[警告] 缺少 twocaptcha 库或 TWOCAPTCHA_API_KEY 环境变量，无法启动打码服务。")
                    else:
                        sitekey = KNOWN_SITEKEY
                        # 尝试动态提取，如果页面成功加载了 iframe 的话
                        try:
                            if await page.locator("iframe[src*='hcaptcha.com']").count() > 0:
                                iframe_src = await page.locator("iframe[src*='hcaptcha.com']").first.get_attribute("src")
                                sitekey_match = re.search(r'sitekey=([^&]+)', iframe_src)
                                if sitekey_match:
                                    sitekey = sitekey_match.group(1)
                                    print(f"[状态] 动态提取 sitekey 成功: {sitekey}")
                        except Exception:
                            print(f"[状态] 动态提取失败，使用已知安全 sitekey: {sitekey}")

                        print(f"[等待] 正在向 2Captcha 云端发送请求... (预计耗时 15-45 秒，请耐心等待)")
                        
                        # 2Captcha 是同步库，使用 asyncio.to_thread 放入后台线程执行
                        result = await asyncio.to_thread(
                            solver.hcaptcha,
                            sitekey=sitekey,
                            url=page.url
                        )
                        
                        token = result.get('code')
                        
                        if token:
                            print("[状态] 成功获取 2Captcha Token！正在执行底层 JavaScript 霸王硬上弓式注入...")
                            
                            # 【核心注入逻辑】: 哪怕页面上没有验证码控件，我们也直接伪造它所需的变量和对象
                            await page.evaluate(f'''
                                const token = "{token}";
                                
                                // 1. 创建或覆盖隐藏的 textarea
                                let textareas = document.querySelectorAll('[name="h-captcha-response"], [name="g-recaptcha-response"]');
                                if (textareas.length === 0) {{
                                    let ta = document.createElement('textarea');
                                    ta.name = 'h-captcha-response';
                                    ta.style.display = 'none';
                                    document.body.appendChild(ta);
                                    textareas = [ta];
                                }}
                                textareas.forEach(el => {{
                                    el.value = token;
                                    el.innerHTML = token;
                                }});
                                
                                // 2. 伪造前端框架常调用的 hcaptcha 对象，骗过页面的 JS 验证
                                window.hcaptcha = {{
                                    getResponse: function() {{ return token; }},
                                    getRespKey: function() {{ return ""; }},
                                    execute: function() {{ return Promise.resolve(token); }},
                                    render: function() {{ return 0; }},
                                    reset: function() {{}}
                                }};
                                
                                // 3. 强行激活认领按钮
                                const btn = document.querySelector(".btn-success");
                                if(btn) {{ 
                                    btn.removeAttribute("disabled"); 
                                    btn.classList.remove("disabled"); 
                                }}
                            ''')
                            print("[状态] 深度伪造与 Token 注入完毕。")
                            await asyncio.sleep(2)
                        else:
                            print("[错误] 2Captcha 识别失败，未返回 Code。")
                except Exception as e:
                    print(f"[错误] 2Captcha API 致命执行异常: {e}")

                await safe_screenshot(page, f"debug_hcaptcha_after_api_loop_{i}.png")
            else:
                print("[状态] 未发现需要验证码的迹象，尝试直接推进。")

            print("[动作] 尝试点击绿色认领按钮...")
            try:
                claim_button = page.locator("button:has-text('Click here to claim'), button:has-text('Complete the captcha'), .btn-success").first
                await claim_button.click(timeout=5000, force=True)
                await asyncio.sleep(2)
                await safe_screenshot(page, f"debug_after_claim_click_loop_{i}.png")
            except Exception as e:
                print(f"[错误] 无法定位或点击绿色按钮: {e}")
                await safe_screenshot(page, f"debug_claim_error_loop_{i}.png")
                break

            try:
                close_ad_btn = page.locator("button:has-text('X'), .close").first
                await close_ad_btn.click(timeout=3000)
                print("[动作] 已关闭广告弹窗。")
            except Exception:
                pass

            print("[等待] 正在等待进度条 (预设 20 秒)...")
            await asyncio.sleep(20)
            await safe_screenshot(page, f"debug_after_progressbar_loop_{i}.png")

            try:
                ok_button = page.locator("button:has-text('OK')").first
                await ok_button.click(timeout=5000)
                print(f"[成功] 第 {i} 次金币收集闭环完成！")
            except Exception as e:
                print(f"[警告] 未检测到 Success 的 OK 按钮: {e}")
                await safe_screenshot(page, f"debug_missing_ok_loop_{i}.png")

            await asyncio.sleep(3)

        print("\n[结束] 脚本执行完毕，准备关闭浏览器。")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
